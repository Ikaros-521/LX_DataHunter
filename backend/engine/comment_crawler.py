"""评论区爬取 + 页面截图。

- B站：bilibili-api SDK 的 comment 模块（纯 API，免登录，按点赞排序取 Top N）
- 抖音：Playwright 打开视频页，拦截 /aweme/v1/web/comment/list/ XHR 响应，
  滚动加载更多；同一浏览器会话顺带完成主页/视频页截图（需登录 Cookie，
  无 Cookie 时调用方应降级跳过抖音路径）
- B站截图：Playwright 访问空间页/视频页截图

Playwright 部分在 Windows + uvicorn 环境下需由调用方放入独立线程的
Proactor 事件循环执行（见 hot_crawler._run_coro_in_thread 模式）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger("lx_datahunter.comment_crawler")

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]

DOUYIN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "Chrome/131.0.0.0 Safari/537.36")

# B站 HTTP 接口对 UA 指纹敏感：不完整的 UA（缺 "(KHTML, like Gecko)"）会触发
# 风控 code=-352，必须用完整浏览器 UA
BILI_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


@dataclass
class CommentItem:
    """跨平台统一的评论结构。"""
    comment_id: str = ""
    user_nickname: str = ""
    text: str = ""
    likes: int = 0
    create_time: datetime | None = None
    ip_location: str = ""
    sub_comment_count: int = 0
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# B站评论（bilibili-api SDK）
# --------------------------------------------------------------------------

def parse_bilibili_reply(reply: dict) -> CommentItem:
    """解析 bilibili-api comment.get_comments 返回的单条 reply（纯函数，便于单测）。"""
    content = reply.get("content", {}) or {}
    member = reply.get("member", {}) or {}
    ctime = reply.get("ctime", 0)
    location = ""
    rc = reply.get("reply_control", {}) or {}
    if rc.get("location"):
        # 形如 "IP属地：广东"
        location = str(rc["location"]).replace("IP属地：", "").strip()
    return CommentItem(
        comment_id=str(reply.get("rpid", "")),
        user_nickname=member.get("uname", ""),
        text=(content.get("message") or "").strip(),
        likes=reply.get("like", 0) or 0,
        create_time=datetime.fromtimestamp(ctime) if ctime else None,
        ip_location=location,
        sub_comment_count=reply.get("rcount", 0) or 0,
        extra={"mid": member.get("mid", "")},
    )


BILI_REPLY_MAIN_URL = "https://api.bilibili.com/x/v2/reply/main"


async def crawl_bilibili_comments(
    aid: int,
    max_comments: int = 100,
    credential=None,
) -> list[CommentItem]:
    """拉取 B站视频评论，按点赞排序，最多 max_comments 条。

    优先走 /x/v2/reply/main 游标接口（mode=3 按点赞）：旧 /x/v2/reply?pn=
    分页接口匿名状态下仅返回前几条热评、翻页即空（2026-09 实测），
    已不可用；主接口失败时回退旧接口（登录态下仍可用）。
    max_comments <= 0 表示全量翻到 is_end。
    """
    headers = {
        "User-Agent": BILI_UA,
        "Referer": "https://www.bilibili.com/",
    }
    sessdata = getattr(credential, "sessdata", None) if credential else None
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"

    unlimited = max_comments <= 0
    items: list[CommentItem] = []
    seen: set[str] = set()
    next_cursor = 0
    first_page = True
    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            while unlimited or len(items) < max_comments:
                data = None
                for attempt in range(3):
                    try:
                        resp = await client.get(BILI_REPLY_MAIN_URL, params={
                            "oid": aid, "type": 1, "mode": 3,
                            "next": next_cursor, "ps": 30,
                        })
                        data = resp.json()
                    except Exception as e:
                        # 非 JSON 响应（412 风控页等）
                        status = getattr(resp, "status_code", "?")
                        logger.warning(
                            f"Bilibili main comments non-json http={status} "
                            f"(attempt {attempt + 1}/3) for av{aid}: {type(e).__name__}")
                        await asyncio.sleep(3.0 * (attempt + 1))
                        continue
                    if data.get("code") == 0:
                        break
                    # -352/-412 等风控码多为瞬态，退避后重试
                    logger.warning(f"Bilibili main comments code={data.get('code')} "
                                   f"(attempt {attempt + 1}/3) for av{aid}")
                    await asyncio.sleep(3.0 * (attempt + 1))
                if not data or data.get("code") != 0:
                    break
                d = data.get("data") or {}
                batch = list(d.get("replies") or [])
                if first_page:
                    # 置顶评论只在首页返回
                    batch = list(d.get("top_replies") or []) + batch
                first_page = False
                if not batch:
                    break
                for reply in batch:
                    c = parse_bilibili_reply(reply)
                    if c.comment_id and c.comment_id in seen:
                        continue
                    seen.add(c.comment_id)
                    items.append(c)
                cursor = d.get("cursor") or {}
                if cursor.get("is_end"):
                    break
                new_next = cursor.get("next") or 0
                if new_next <= next_cursor:
                    break  # 游标异常，防死循环
                next_cursor = new_next
                await asyncio.sleep(0.3)
    except Exception as e:
        logger.warning(f"Bilibili main comments failed for av{aid}: {e}")

    if items:
        return items if unlimited else items[:max_comments]
    # 主接口一条都没拿到（风控/参数异常）→ 等待冷却后回退旧分页接口
    await asyncio.sleep(2.0)
    return await _crawl_bilibili_comments_legacy(aid, max_comments, credential)


async def _crawl_bilibili_comments_legacy(
    aid: int,
    max_comments: int = 100,
    credential=None,
) -> list[CommentItem]:
    """旧 /x/v2/reply?pn= 分页接口（SDK 封装）；匿名已基本失效，仅作回退。"""
    from bilibili_api import comment as bili_comment, sync
    from bilibili_api.comment import CommentResourceType, OrderType

    unlimited = max_comments <= 0
    items: list[CommentItem] = []
    page = 1
    # 每页约 20 条，多取一页余量；全量模式由 replies 空页自然终止
    max_pages = 1000 if unlimited else (max_comments // 20) + 2
    while (unlimited or len(items) < max_comments) and page <= max_pages:
        try:
            data = sync(bili_comment.get_comments(
                oid=aid,
                type_=CommentResourceType.VIDEO,
                page_index=page,
                order=OrderType.LIKE,
                credential=credential,
            ))
        except Exception as e:
            logger.warning(f"Bilibili comments page {page} failed for av{aid}: {e}")
            break
        replies = (data or {}).get("replies") or []
        if not replies:
            break
        for r in replies:
            items.append(parse_bilibili_reply(r))
        page += 1
        await asyncio.sleep(0.3)

    # 去重（rpid）并截断
    seen: set[str] = set()
    unique: list[CommentItem] = []
    for c in items:
        if c.comment_id and c.comment_id in seen:
            continue
        seen.add(c.comment_id)
        unique.append(c)
    return unique if unlimited else unique[:max_comments]


# --------------------------------------------------------------------------
# B站弹幕（bilibili-api SDK，当前弹幕池全量）
# --------------------------------------------------------------------------

@dataclass
class DanmakuItem:
    """跨平台统一的弹幕结构（目前仅B站）。"""
    danmaku_id: str = ""
    text: str = ""
    send_time: datetime | None = None
    dm_time: float = 0.0        # 视频内出现位置（秒）
    mode: int = 1               # 1=滚动 4=底部 5=顶部
    font_size: int = 25
    color: str = ""
    user_hash: str = ""
    pool: int = 0
    extra: dict = field(default_factory=dict)


def parse_bilibili_danmaku(dm) -> DanmakuItem:
    """SDK Danmaku 对象 → 统一结构（纯函数，便于单测）。"""
    def _int(v, default: int) -> int:
        return int(getattr(v, "value", v) or default)

    send_ts = float(getattr(dm, "send_time", 0) or 0)
    return DanmakuItem(
        danmaku_id=str(getattr(dm, "id_", 0) or getattr(dm, "id_str", "") or ""),
        text=(getattr(dm, "text", "") or "").strip(),
        send_time=datetime.fromtimestamp(send_ts) if send_ts else None,
        dm_time=float(getattr(dm, "dm_time", 0) or 0),
        mode=_int(getattr(dm, "mode", 1), 1),
        font_size=_int(getattr(dm, "font_size", 25), 25),
        color=str(getattr(dm, "color", "") or "").lstrip("#"),
        user_hash=str(getattr(dm, "crc32_id", "") or ""),
        pool=int(getattr(dm, "pool", 0) or 0),
        extra={"weight": int(getattr(dm, "weight", 0) or 0)},
    )


async def crawl_bilibili_danmakus(
    aid: int,
    max_danmakus: int = -1,
    credential=None,
) -> tuple[list[DanmakuItem], str]:
    """拉取 B站视频当前弹幕池弹幕（全量），最多 max_danmakus 条。

    返回 (弹幕列表, 错误说明)。错误说明为空表示成功；
    -403（充电专属/权限内容）等无法重试的错误转成友好文案，
    调用方可据此跳过该视频并在报告中说明。
    max_danmakus <= 0 表示不限量（弹幕接口按 6 分钟分段一次取全，
    本身即全量返回，这里只做条数截断与去重）。
    """
    from bilibili_api import sync, video as bili_video

    v = bili_video.Video(aid=aid, credential=credential)
    try:
        raw = sync(v.get_danmakus())
    except Exception as e:
        msg = str(e)
        if "-403" in msg:
            logger.info(f"Bilibili danmakus permission denied for av{aid}: {msg}")
            return [], "无弹幕访问权限（可能为充电专属/受限内容）"
        logger.warning(f"Bilibili danmakus failed for av{aid}: {e}")
        return [], f"弹幕接口异常：{msg[:120]}"

    seen: set[str] = set()
    unique: list[DanmakuItem] = []
    for d in raw:
        item = parse_bilibili_danmaku(d)
        if not item.text:
            continue
        if item.danmaku_id and item.danmaku_id in seen:
            continue
        seen.add(item.danmaku_id)
        unique.append(item)
    items = unique if max_danmakus <= 0 else unique[:max_danmakus]
    return items, ""


# --------------------------------------------------------------------------
# 抖音评论（Playwright XHR 拦截）
# --------------------------------------------------------------------------

def parse_douyin_comment_payload(data: dict) -> list[CommentItem]:
    """解析抖音 /aweme/v1/web/comment/list/ XHR JSON（纯函数，便于单测）。

    响应结构：{"status_code":0, "comments":[{"cid","text","create_time",
    "digg_count","user":{"nickname"},"ip_label","reply_comment_total"}],
    "cursor":..,"has_more":..,"total":..}
    """
    items: list[CommentItem] = []
    for c in data.get("comments") or []:
        if not isinstance(c, dict):
            continue
        user = c.get("user", {}) or {}
        ct = c.get("create_time", 0)
        items.append(CommentItem(
            comment_id=str(c.get("cid", "")),
            user_nickname=user.get("nickname", ""),
            text=(c.get("text") or "").strip(),
            likes=c.get("digg_count", 0) or 0,
            create_time=datetime.fromtimestamp(ct) if ct else None,
            ip_location=c.get("ip_label", "") or "",
            sub_comment_count=c.get("reply_comment_total", 0) or 0,
        ))
    return items


async def crawl_douyin_comments_batch(
    profile_url: str | None,
    video_urls: list[str],
    max_per_video: int = 100,
    cookie_str: str | None = None,
    screenshot_dir: Path | None = None,
    headless: bool = True,
    on_video_done=None,
) -> tuple[dict[str, list[CommentItem]], dict[str, str]]:
    """一个 Playwright 会话完成抖音调研的浏览器侧全部工作：

    1. （可选）goto 主页并截图 → screenshot_dir/profile_*.png
    2. 逐视频：goto → 拦截评论 XHR → 滚动加载 → 截图 → 返回评论

    返回 ({video_url: [CommentItem]}, {url: 截图文件名})；
    单个视频/截图失败不影响整体（对应项缺省）。
    max_per_video <= 0 表示每视频全量爬取（滚动到评论区见底）。
    on_video_done(url, comment_count) 用于进度回调。
    """
    from playwright.async_api import async_playwright

    comments: dict[str, list[CommentItem]] = {u: [] for u in video_urls}
    screenshots: dict[str, str] = {}
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        launch_args: dict = {"headless": headless, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        chrome_exe = next((cp for cp in CHROME_PATHS if os.path.exists(cp)), None)
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=DOUYIN_UA, locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        if cookie_str:
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    k, _, v = item.partition("=")
                    try:
                        await context.add_cookies([{
                            "name": k.strip(), "value": v.strip(),
                            "domain": ".douyin.com", "path": "/",
                        }])
                    except Exception:
                        pass

        page = await context.new_page()

        # ---- 主页截图 ----
        if profile_url and screenshot_dir:
            try:
                try:
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    logger.warning(f"Douyin profile load incomplete: {e}")
                await asyncio.sleep(4)
                out = screenshot_dir / f"profile_{_safe_filename(profile_url.split('/')[-1] or 'home')}.png"
                await page.screenshot(path=str(out))
                screenshots[profile_url] = out.name
                logger.info(f"Douyin profile screenshot: {out.name}")
            except Exception as e:
                logger.warning(f"Douyin profile screenshot failed: {e}")

        # ---- 逐视频：评论 + 截图 ----
        for idx, url in enumerate(video_urls, 1):
            xhr_pages: list[dict] = []

            async def on_response(response, _store=xhr_pages):
                if "/aweme/v1/web/comment/list" in response.url:
                    try:
                        _store.append(await response.json())
                    except Exception:
                        pass

            page.on("response", on_response)
            try:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    logger.warning(f"Douyin video page load incomplete: {url}: {e}")
                await asyncio.sleep(4)

                # 滚动评论区触发分页加载；不限量模式滚动上限放大，
                # 依靠 has_more=0 / 连续无新增自然终止
                unlimited = max_per_video <= 0
                no_new = 0
                prev_total = 0
                for _ in range(250 if unlimited else 15):
                    total = sum(len(parse_douyin_comment_payload(d)) for d in xhr_pages)
                    if not unlimited and total >= max_per_video:
                        break
                    if total == prev_total:
                        no_new += 1
                        if no_new >= 4:
                            break
                    else:
                        no_new = 0
                    prev_total = total
                    if xhr_pages and not any(p.get("has_more") for p in xhr_pages):
                        break
                    await page.evaluate("window.scrollBy(0, 1200)")
                    await asyncio.sleep(1.5)

                seen: set[str] = set()
                merged: list[CommentItem] = []
                for d in xhr_pages:
                    for c in parse_douyin_comment_payload(d):
                        if c.comment_id and c.comment_id in seen:
                            continue
                        seen.add(c.comment_id)
                        merged.append(c)
                merged.sort(key=lambda c: c.likes, reverse=True)
                comments[url] = merged if unlimited else merged[:max_per_video]

                if screenshot_dir:
                    out = screenshot_dir / f"video_{idx}_{_safe_filename(url.rstrip('/').split('/')[-1])}.png"
                    await page.screenshot(path=str(out))
                    screenshots[url] = out.name

                logger.info(f"Douyin comments for {url}: {len(comments[url])} captured")
            except Exception as e:
                logger.warning(f"Douyin comment crawl failed for {url}: {e}")
            finally:
                page.remove_listener("response", on_response)
                if on_video_done:
                    try:
                        await on_video_done(url, len(comments.get(url) or []))
                    except Exception:
                        pass

        await browser.close()
    return comments, screenshots


# --------------------------------------------------------------------------
# 页面截图（Playwright）
# --------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name)[:80]


async def screenshot_pages(
    urls: list[tuple[str, Path]],
    cookie_str: str | None = None,
    cookie_domain: str = ".douyin.com",
    headless: bool = True,
) -> dict[str, str]:
    """批量截图。urls 为 [(标签, 输出文件路径)]，返回 {标签: 文件名(相对调用目录)}。

    抖音页面传 cookie_domain=".douyin.com"；B站页面传 ".bilibili.com"。
    单页失败不影响其他页。
    """
    from playwright.async_api import async_playwright

    saved: dict[str, str] = {}
    if not urls:
        return saved

    async with async_playwright() as p:
        launch_args: dict = {"headless": headless, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        chrome_exe = next((cp for cp in CHROME_PATHS if os.path.exists(cp)), None)
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=DOUYIN_UA, locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        if cookie_str:
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    k, _, v = item.partition("=")
                    try:
                        await context.add_cookies([{
                            "name": k.strip(), "value": v.strip(),
                            "domain": cookie_domain, "path": "/",
                        }])
                    except Exception:
                        pass

        page = await context.new_page()
        for label, out_path in urls:
            url = label  # label 即页面 URL，文件名由调用方定
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    logger.warning(f"Screenshot page load incomplete: {url}: {e}")
                await asyncio.sleep(3)
                await page.screenshot(path=str(out_path), full_page=False)
                saved[url] = out_path.name
                logger.info(f"Screenshot saved: {out_path.name}")
            except Exception as e:
                logger.warning(f"Screenshot failed for {url}: {e}")
        await browser.close()
    return saved
