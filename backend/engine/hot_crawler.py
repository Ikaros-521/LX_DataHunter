"""热门内容爬取 — 抓取平台排行榜/热门内容"""
import asyncio
import logging

from bilibili_api import rank, hot, sync, Credential
from bilibili_api.rank import RankDayType

from backend.models import Platform, ContentType
from backend.engine.platforms.base import ContentItem

logger = logging.getLogger("fapai.hot_crawler")

# 各平台预定义分类
HOT_CATEGORIES = {
    "bilibili": [
        {"id": "ranking", "name": "全站排行榜", "desc": "B站每日综合排行榜 Top 100"},
        {"id": "hot", "name": "热门视频", "desc": "当前正在流行的热门视频"},
    ],
    "douyin": [
        {"id": "hot", "name": "热点榜", "desc": "抖音实时热点榜单"},
        {"id": "search", "name": "搜索", "desc": "输入关键词搜索视频 (需在下方输入搜索词)"},
    ],
}

PLATFORM_LABELS = {
    "bilibili": "B站",
    "douyin": "抖音",
}


def get_categories(platform: str) -> list[dict]:
    """获取指定平台的热门分类列表"""
    return HOT_CATEGORIES.get(platform, [])


async def crawl_hot(
    platform: str,
    category: str,
    max_items: int = 50,
    credential_str: str | None = None,
    progress_callback=None,
    keyword: str = "",
) -> tuple[str, list[ContentItem]]:
    """
    爬取热门内容。返回 (creator_label, items)
    """
    label = PLATFORM_LABELS.get(platform, platform)
    cat_info = next((c for c in HOT_CATEGORIES.get(platform, []) if c["id"] == category), {})
    cat_name = cat_info.get("name", category)
    if keyword:
        cat_name = f"{cat_name}:{keyword}"
    creator_label = f"[{label}{cat_name}]"

    logger.info(f"Hot crawl: platform={platform}, category={category}, max={max_items}, keyword={keyword}")

    items: list[ContentItem] = []

    if platform == "bilibili":
        items = await _crawl_bilibili_hot(category, max_items, credential_str)
    elif platform == "douyin":
        items = await _crawl_douyin_hot(category, max_items, credential_str, keyword)

    if max_items > 0 and len(items) > max_items:
        items = items[:max_items]

    logger.info(f"Hot crawl done: {creator_label}, {len(items)} items")
    return creator_label, items


# ---- B站热门 ----

def _make_bili_cred(cookie_str: str | None) -> Credential | None:
    if not cookie_str:
        return None
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, _, v = item.partition("=")
            cookies[k.strip()] = v.strip()
    return Credential(
        sessdata=cookies.get("SESSDATA", ""),
        bili_jct=cookies.get("bili_jct", ""),
        buvid3=cookies.get("buvid3", ""),
        dedeuserid=cookies.get("DedeUserID", ""),
        buvid4=cookies.get("buvid4", ""),
    )


async def _crawl_bilibili_hot(category: str, max_items: int, cookie_str: str | None) -> list[ContentItem]:
    cred = _make_bili_cred(cookie_str)

    if category == "ranking":
        return await _bili_ranking(max_items, cred)
    elif category == "hot":
        return await _bili_hot(max_items, cred)
    return []


async def _bili_ranking(max_items: int, cred: Credential | None) -> list[ContentItem]:
    """B站全站排行榜"""
    from datetime import datetime

    try:
        data = sync(rank.get_rank())
    except Exception as e:
        logger.error(f"B站 rank API failed: {e}")
        return []

    items = []
    for v in data.get("list", []):
        aid = str(v.get("aid", ""))
        items.append(ContentItem(
            content_id=aid,
            content_type=ContentType.VIDEO,
            title=v.get("title", ""),
            cover_url=v.get("pic", ""),
            create_time=datetime.fromtimestamp(v.get("pubdate", 0)) if v.get("pubdate") else None,
            duration_sec=_parse_duration(v.get("duration", "0:00")),
            url=f"https://www.bilibili.com/video/av{aid}",
            likes=0,
            comments=0,
            shares=0,
            plays=v.get("play", 0) or 0,
            collects=v.get("favorites", 0) or 0,
            extra_data={
                "bvid": v.get("bvid", ""),
                "author": v.get("owner", {}).get("name", ""),
                "pts": v.get("pts", 0),
            },
        ))
        if max_items > 0 and len(items) >= max_items:
            break

    # 批量获取 stat
    if items:
        await _enrich_bili_stats(items, cred)
    return items


async def _bili_hot(max_items: int, cred: Credential | None) -> list[ContentItem]:
    """B站热门视频"""
    from datetime import datetime

    try:
        data = sync(hot.get_hot_videos())
    except Exception as e:
        logger.error(f"B站 hot API failed: {e}")
        return []

    items = []
    for v in data.get("list", []):
        aid = str(v.get("aid", ""))
        stat = v.get("stat", {})
        items.append(ContentItem(
            content_id=aid,
            content_type=ContentType.VIDEO,
            title=v.get("title", ""),
            cover_url=v.get("pic", ""),
            create_time=datetime.fromtimestamp(v.get("pubdate", 0)) if v.get("pubdate") else None,
            duration_sec=_parse_duration(v.get("duration", "0:00")),
            url=v.get("short_link_v2", "") or f"https://www.bilibili.com/video/av{aid}",
            likes=stat.get("like", 0),
            comments=stat.get("reply", 0),
            shares=stat.get("share", 0),
            plays=stat.get("view", 0),
            collects=stat.get("favorite", 0),
            extra_data={
                "bvid": v.get("bvid", ""),
                "author": v.get("owner", {}).get("name", ""),
                "danmaku": stat.get("danmaku", 0),
                "coin": stat.get("coin", 0),
                "rcmd_reason": v.get("rcmd_reason", {}).get("content", ""),
            },
        ))
        if max_items > 0 and len(items) >= max_items:
            break
    return items



async def _enrich_bili_stats(items: list[ContentItem], cred: Credential | None):
    """补充 B站热门视频的互动数据"""
    from bilibili_api import video as bvideo
    enriched = 0
    for item in items:
        bvid = item.extra_data.get("bvid", "")
        if not bvid:
            continue
        try:
            v = bvideo.Video(bvid=bvid, credential=cred)
            info = sync(v.get_info())
            stat = info.get("stat", {})
            if stat:
                item.likes = stat.get("like", 0)
                item.comments = stat.get("reply", 0)
                item.shares = stat.get("share", 0)
                item.plays = stat.get("view", item.plays)
                item.collects = stat.get("favorite", 0)
                item.extra_data["danmaku"] = stat.get("danmaku", 0)
                item.extra_data["coin"] = stat.get("coin", 0)
            enriched += 1
            if enriched % 10 == 0:
                logger.info(f"Hot stat enrich: {enriched}/{len(items)}")
                await asyncio.sleep(0.2)
        except Exception:
            pass
    logger.info(f"Hot stat enriched: {enriched}/{len(items)}")


async def _crawl_douyin_hot(category: str, max_items: int, cookie_str: str | None, keyword: str = "") -> list[ContentItem]:
    """抖音热点/搜索 — Playwright 加载页面 + 逐个视频页提取 __INITIAL_STATE__"""
    import os, json, re
    from datetime import datetime
    from playwright.async_api import async_playwright

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)

    xhr_data: list[dict] = []
    group_ids: list[str] = []

    async with async_playwright() as p:
        launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            locale="zh-CN", viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        async def on_response(response):
            if "/aweme/v1/web/hot/search/list" in response.url or "/aweme/v1/web/search/item" in response.url:
                try:
                    xhr_data.append(await response.json())
                except Exception:
                    pass
        page.on("response", on_response)

        # 加载页面
        if category == "search" and keyword:
            # 搜索
            search_url = f"https://www.douyin.com/search/{keyword}?type=general"
            logger.info(f"Loading Douyin search: {search_url}")
        else:
            search_url = "https://www.douyin.com/hot"
            logger.info("Loading Douyin hot page...")

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"Douyin page load: {e}")
        await asyncio.sleep(5)

        # 滚动加载更多
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

        # 提取 group_id 列表
        for data in xhr_data:
            data_section = data.get("data", {})
            # 热点榜
            for t in data_section.get("trending_list", []):
                gid = str(t.get("group_id", ""))
                if gid and gid not in group_ids:
                    group_ids.append(gid)
            # 搜索结果
            for item in data_section.get("data", []) or data_section.get("aweme_list", []):
                aweme = item.get("aweme_info", item)
                gid = str(aweme.get("aweme_id", "") or aweme.get("group_id", ""))
                if gid and gid not in group_ids:
                    group_ids.append(gid)

        if not group_ids:
            logger.warning("No group_ids found, trying page content...")
            html = await page.content()
            # Try to find any aweme_id in the HTML
            ids = re.findall(r'"aweme_id"\s*:\s*"(\d+)"', html)
            group_ids = list(dict.fromkeys(ids))[:max_items]

        logger.info(f"Found {len(group_ids)} video IDs, enriching stats via video pages...")

        # 逐个视频页提取 stat
        items: list[ContentItem] = []
        for gid in group_ids[:max_items]:
            try:
                vid_url = f"https://www.douyin.com/video/{gid}"
                await page.goto(vid_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                html = await page.content()
                match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>', html, re.DOTALL)
                if not match:
                    match = re.search(r'"aweme_detail":(\{.+?\})\s*,\s*"aweme_list"', html, re.DOTALL)
                if not match:
                    continue

                state_str = match.group(1)
                # 尝试解析（可能不完整）
                try:
                    state = json.loads(state_str)
                except json.JSONDecodeError:
                    continue

                aweme = state.get("aweme_detail", state)
                stats = aweme.get("statistics", {})
                video = aweme.get("video", {})
                author = aweme.get("author", {})
                music = aweme.get("music", {})

                ct = aweme.get("create_time", 0)
                create_dt = datetime.fromtimestamp(ct) if ct else None

                items.append(ContentItem(
                    content_id=gid,
                    content_type=ContentType.VIDEO,
                    title=aweme.get("desc", ""),
                    cover_url=(video.get("cover", {}) or {}).get("url_list", [""])[0],
                    create_time=create_dt,
                    duration_sec=round((video.get("duration", 0) or 0) / 1000, 1),
                    url=f"https://www.douyin.com/video/{gid}",
                    tags=[],
                    music_title=(music or {}).get("title", ""),
                    likes=stats.get("digg_count", 0),
                    comments=stats.get("comment_count", 0),
                    shares=stats.get("share_count", 0),
                    plays=stats.get("play_count", 0),
                    collects=stats.get("collect_count", 0),
                    extra_data={"author": author.get("nickname", ""), "source": category},
                ))
                logger.info(f"Video {len(items)}/{len(group_ids[:max_items])}: {aweme.get('desc', '')[:30]}... likes={stats.get('digg_count', 0)}")

            except Exception as e:
                logger.warning(f"Video {gid} stat failed: {e}")
                continue

        await browser.close()

    logger.info(f"Douyin {category} parsed: {len(items)} items")
    return items


def _parse_duration(length) -> float:
    """解析时长：支持 "mm:ss" 字符串或整数秒"""
    if isinstance(length, (int, float)):
        return float(length)
    if not length:
        return 0
    parts = str(length).split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0
