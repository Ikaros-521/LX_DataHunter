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
        {"id": "weekly", "name": "每周必看", "desc": "B站官方每周精选视频"},
    ],
    "douyin": [
        {"id": "hot", "name": "抖音热点", "desc": "抖音热点内容 (需登录)"},
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
) -> tuple[str, list[ContentItem]]:
    """
    爬取热门内容。返回 (creator_label, items)
    """
    label = PLATFORM_LABELS.get(platform, platform)
    cat_info = next((c for c in HOT_CATEGORIES.get(platform, []) if c["id"] == category), {})
    cat_name = cat_info.get("name", category)
    creator_label = f"[{label}{cat_name}]"

    logger.info(f"Hot crawl: platform={platform}, category={category}, max={max_items}")

    items: list[ContentItem] = []

    if platform == "bilibili":
        items = await _crawl_bilibili_hot(category, max_items, credential_str)
    elif platform == "douyin":
        items = await _crawl_douyin_hot(max_items, credential_str)

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
    elif category == "weekly":
        return await _bili_weekly(max_items, cred)
    return []


async def _bili_ranking(max_items: int, cred: Credential | None) -> list[ContentItem]:
    """B站全站排行榜"""
    from datetime import datetime

    try:
        data = sync(rank.get_rank(credential=cred))
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
        data = sync(hot.get_hot_videos(credential=cred))
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


async def _bili_weekly(max_items: int, cred: Credential | None) -> list[ContentItem]:
    """B站每周必看"""
    from datetime import datetime

    try:
        data = sync(hot.get_weekly_hot_videos_list(credential=cred))
    except Exception as e:
        logger.error(f"B站 weekly API failed: {e}")
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


async def _crawl_douyin_hot(max_items: int, cookie_str: str | None) -> list[ContentItem]:
    """抖音热点 (Phase 2)"""
    logger.warning("Douyin hot crawl not yet implemented")
    return []


def _parse_duration(length: str) -> float:
    parts = length.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0
