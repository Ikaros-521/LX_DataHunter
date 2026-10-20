"""单视频链接爬取 — 解析 B站/抖音视频链接，抓取单条视频详情。

与创作者/热门爬取共用 ContentItem/CreatorInfo 结构，方便直接写入
ContentSnapshot。B站走 bilibili-api 拉详情；抖音与创作者爬取一致，
用 Playwright 打开视频页并捕获 /aweme/v1/web/aweme/detail 的 XHR 响应。
"""
import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx

from backend.models import Platform, ContentType
from backend.engine.platforms.base import ContentItem, CreatorInfo

logger = logging.getLogger("lx_datahunter.single_video")

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

_BILI_BV_RE = re.compile(r"(?:bilibili\.com|b23\.tv)/(?:video/)?(BV[0-9A-Za-z]{8,})", re.I)
_BILI_AV_RE = re.compile(r"(?:bilibili\.com|b23\.tv)/(?:video/)?av(\d+)", re.I)
_DOUYIN_RE = re.compile(r"(?:www\.|m\.)?(?:ies)?douyin\.com/(?:share/)?(?:video|note)/(\d{5,})")

# 已知短链域名：路径不含视频 id，需要跟随重定向解析
_SHORT_HOSTS = {"b23.tv", "v.douyin.com"}

_PLATFORM_HOSTS = ("bilibili.com", "b23.tv", "douyin.com", "iesdouyin.com")

_PARSE_ERROR = (
    "无法识别的视频链接。支持：B站视频页 (bilibili.com/video/BVxxx)、b23.tv 短链、"
    "抖音视频页 (douyin.com/video/xxx)、抖音分享短链 (v.douyin.com/xxx)"
)


def _match_platform(url: str) -> tuple[Platform, str, str] | None:
    """从 URL 提取 (平台, 视频 id, 规范链接)，识别不了返回 None。"""
    m = _BILI_BV_RE.search(url)
    if m:
        bvid = m.group(1)
        return Platform.BILIBILI, bvid, f"https://www.bilibili.com/video/{bvid}"
    m = _BILI_AV_RE.search(url)
    if m:
        aid = m.group(1)
        return Platform.BILIBILI, f"av{aid}", f"https://www.bilibili.com/video/av{aid}"
    m = _DOUYIN_RE.search(url)
    if m:
        aweme_id = m.group(1)
        return Platform.DOUYIN, aweme_id, f"https://www.douyin.com/video/{aweme_id}"
    return None


async def _resolve_redirect(url: str) -> str:
    """跟随重定向取最终 URL（短链展开）。"""
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=15, headers=_HTTP_HEADERS
    ) as client:
        resp = await client.get(url)
        final = str(resp.url)
    logger.info(f"Resolved short link: {url} -> {final}")
    return final


async def parse_video_url(raw: str) -> tuple[Platform, str, str]:
    """解析用户输入的视频链接 → (平台, 视频 id, 规范链接)。

    识别失败抛 ValueError（API 层转 400）。
    """
    url = (raw or "").strip().strip("\"'")
    if not url:
        raise ValueError("请输入视频链接")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except ValueError:
        raise ValueError(_PARSE_ERROR)
    host = (parsed.netloc or "").lower()
    if not host or "." not in host:
        raise ValueError(_PARSE_ERROR)

    result = _match_platform(url)
    if result:
        return result

    # 短链域名（路径不含视频 id）或未知域名 → 跟随重定向后再识别；
    # 已知平台域名的非视频页（如创作者主页）则直接报错
    if not (host in _SHORT_HOSTS or any(host.endswith(h) for h in ("b23.tv", "v.douyin.com"))):
        if any(host == h or host.endswith("." + h) for h in _PLATFORM_HOSTS):
            raise ValueError(
                "识别到平台域名但不是视频链接，请粘贴具体的视频页地址"
                "（如 bilibili.com/video/BVxxx 或 douyin.com/video/xxx）"
            )

    # 其余域名当作短链尝试展开一次
    try:
        resolved = await _resolve_redirect(url)
    except Exception as e:
        logger.warning(f"Short link resolve failed for {url}: {e}")
        raise ValueError(_PARSE_ERROR)
    result = _match_platform(resolved)
    if result:
        return result
    raise ValueError(_PARSE_ERROR)


async def crawl_single_video(
    url: str,
    cookie_str: str | None = None,
    headless: bool = True,
) -> tuple[Platform, str, CreatorInfo, ContentItem]:
    """解析并抓取单条视频。返回 (平台, 视频 id, 作者信息, 内容项)。

    抖音路径在独立线程的 Proactor loop 中跑 Playwright（原因见
    hot_crawler._run_coro_in_thread 注释），不阻塞 uvicorn 事件循环。
    """
    platform, video_id, canonical = await parse_video_url(url)
    logger.info(f"Single video crawl: platform={platform.value}, id={video_id}")

    if platform is Platform.BILIBILI:
        creator_info, item = await _crawl_bilibili(video_id, cookie_str)
    elif platform is Platform.DOUYIN:
        from backend.engine.hot_crawler import _run_coro_in_thread
        loop = asyncio.get_running_loop()
        creator_info, item = await loop.run_in_executor(
            None, _run_coro_in_thread, _crawl_douyin(video_id, cookie_str, headless)
        )
    else:
        raise ValueError(f"平台 {platform.value} 暂不支持单视频链接爬取")

    if not item.url:
        item.url = canonical
    logger.info(f"Single video done: '{item.title[:50]}' by '{creator_info.nickname}'")
    return platform, video_id, creator_info, item


# ---- B站 ----

async def _crawl_bilibili(video_id: str, cookie_str: str | None) -> tuple[CreatorInfo, ContentItem]:
    from bilibili_api import video
    from backend.engine.platforms.bilibili import _make_credential

    cred = _make_credential(cookie_str)
    if video_id.startswith(("BV", "bv")):
        v = video.Video(bvid=video_id, credential=cred)
    else:
        v = video.Video(aid=int(video_id[2:]), credential=cred)

    info = await v.get_info()
    stat = info.get("stat", {}) or {}
    owner = info.get("owner", {}) or {}
    bvid = info.get("bvid", "") or (video_id if video_id.startswith("BV") else "")
    aid = str(info.get("aid", "") or (video_id[2:] if video_id.startswith("av") else ""))
    pubdate = info.get("pubdate", 0)

    tags: list[str] = []
    try:
        tag_list = await v.get_tags()
        tags = [t.get("tag_name", "") for t in tag_list if t.get("tag_name")]
    except Exception as e:
        logger.warning(f"get_tags failed: {e}")

    mid = str(owner.get("mid", ""))
    followers = 0
    if mid:
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HTTP_HEADERS) as client:
                r = await client.get(
                    "https://api.bilibili.com/x/relation/stat", params={"vmid": mid}
                )
                followers = (r.json().get("data") or {}).get("follower", 0)
        except Exception as e:
            logger.warning(f"relation/stat failed for {mid}: {e}")

    creator_info = CreatorInfo(
        nickname=owner.get("name", ""),
        signature="",
        avatar_url=owner.get("face", ""),
        follower_count=followers,
        extra_info={"mid": mid},
    )
    item = ContentItem(
        content_id=aid or bvid,
        content_type=ContentType.VIDEO,
        title=info.get("title", ""),
        cover_url=info.get("pic", ""),
        create_time=datetime.fromtimestamp(pubdate) if pubdate else None,
        duration_sec=float(info.get("duration", 0) or 0),
        url=f"https://www.bilibili.com/video/{bvid or 'av' + aid}",
        tags=tags,
        likes=stat.get("like", 0),
        comments=stat.get("reply", 0),
        shares=stat.get("share", 0),
        plays=stat.get("view", 0),
        collects=stat.get("favorite", 0),
        extra_data={
            "bvid": bvid,
            "aid": aid,
            "danmaku": stat.get("danmaku", 0),
            "coin": stat.get("coin", 0),
            "description": info.get("desc", ""),
        },
    )
    return creator_info, item


# ---- 抖音 ----

async def _crawl_douyin(
    aweme_id: str, cookie_str: str | None, headless: bool
) -> tuple[CreatorInfo, ContentItem]:
    """Playwright 打开视频页，捕获 aweme/detail XHR。"""
    import os
    from playwright.async_api import async_playwright

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)

    video_url = f"https://www.douyin.com/video/{aweme_id}"
    captured: list[dict] = []

    async with async_playwright() as p:
        launch_args = {"headless": headless, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=_HTTP_HEADERS["User-Agent"],
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080},
        )
        # 反自动化检测：模式同 DouyinAdapter
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        if cookie_str:
            injected = 0
            for kv in cookie_str.split(";"):
                kv = kv.strip()
                if "=" in kv:
                    k, _, val = kv.partition("=")
                    try:
                        await context.add_cookies([{
                            "name": k.strip(), "value": val.strip(),
                            "domain": ".douyin.com", "path": "/",
                        }])
                        injected += 1
                    except Exception:
                        pass
            logger.info(f"Injected {injected} cookies into Douyin context")

        page = await context.new_page()

        async def on_response(response):
            if "/aweme/v1/web/aweme/detail" in response.url:
                try:
                    data = await response.json()
                    if data.get("aweme_detail"):
                        captured.append(data["aweme_detail"])
                        logger.info("Captured aweme/detail XHR")
                except Exception:
                    pass

        page.on("response", on_response)

        logger.info(f"Navigating to: {video_url}")
        try:
            await page.goto(video_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logger.warning(f"Page navigation incomplete: {e}")

        # 最多再等 25s 让 XHR 到达
        for _ in range(25):
            if captured:
                break
            if "login" in page.url or "passport" in page.url:
                await browser.close()
                raise RuntimeError(
                    "抖音需要登录态才能访问该视频，请在「凭证管理」添加抖音 Cookie 后重试"
                )
            await asyncio.sleep(1)

        await browser.close()

    if not captured:
        raise RuntimeError(
            "未能从抖音页面获取视频数据（可能需要登录态或视频已删除）。"
            "请在「凭证管理」添加抖音 Cookie 后重试"
        )

    aweme = captured[0]
    return _parse_aweme(aweme)


def _parse_aweme(aweme: dict) -> tuple[CreatorInfo, ContentItem]:
    """把抖音 aweme 详情 dict 转成 (CreatorInfo, ContentItem)。字段同创作者爬取。"""
    author = aweme.get("author", {}) or {}
    stats = aweme.get("statistics", {}) or {}
    video = aweme.get("video", {}) or {}
    music = aweme.get("music", {}) or {}

    tags = [
        t.get("hashtag_name", "")
        for t in aweme.get("text_extra", []) or []
        if t.get("hashtag_name")
    ]

    ct = aweme.get("create_time", 0)
    create_dt = datetime.fromtimestamp(ct) if ct else None

    cover = ""
    if video.get("cover"):
        urls = video["cover"].get("url_list", [])
        if urls:
            cover = urls[0]

    images = []
    for img in (aweme.get("image_infos") or {}).values():
        urls = img.get("url_list", [])
        if urls:
            images.append(urls[0])

    aid = aweme.get("aweme_id", "")
    creator_info = CreatorInfo(
        nickname=author.get("nickname", ""),
        signature=author.get("signature", ""),
        avatar_url=(author.get("avatar_thumb", {}) or {}).get("url_list", [""])[0],
        follower_count=author.get("follower_count", 0),
        extra_info={"uid": author.get("uid", ""), "sec_uid": author.get("sec_uid", "")},
    )
    item = ContentItem(
        content_id=aid,
        content_type=ContentType.IMAGE if images else ContentType.VIDEO,
        title=aweme.get("desc", ""),
        cover_url=cover,
        create_time=create_dt,
        duration_sec=round(video.get("duration", 0) / 1000, 1),
        url=f"https://www.douyin.com/video/{aid}" if aid else "",
        tags=tags,
        music_title=music.get("title", ""),
        likes=stats.get("digg_count", 0),
        comments=stats.get("comment_count", 0),
        shares=stats.get("share_count", 0),
        plays=stats.get("play_count", 0),
        collects=stats.get("collect_count", 0),
        extra_data={"images": images},
    )
    return creator_info, item
