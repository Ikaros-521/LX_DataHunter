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
        {"id": "search", "name": "搜索", "desc": "关键词搜索 (需输入搜索词 + 浏览器登录)"},
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
    """抖音热点/搜索 — Playwright 加载页面 + 点击筛选器"""
    import os, re
    from datetime import datetime
    from playwright.async_api import async_playwright

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)

    items: list[ContentItem] = []

    async with async_playwright() as p:
        headless = False if (category == "search") else True
        launch_args = {"headless": headless, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            locale="zh-CN", viewport={"width": 1280, "height": 800},
        )

        # 注入 Cookie (必须在 goto 之前)
        cookie_count = 0
        douyin_auth_keys = {"sso_uid_tt", "sessionid", "sessionid_ss", "sid_guard", "uid_tt", "passport_csrf_token"}
        has_auth_cookies = False
        if cookie_str:
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    k, _, v = item.partition("=")
                    try:
                        await context.add_cookies([{"name": k.strip(), "value": v.strip(), "domain": ".douyin.com", "path": "/"}])
                        cookie_count += 1
                        if k.strip() in douyin_auth_keys:
                            has_auth_cookies = True
                    except Exception:
                        pass
        logger.info(f"Injected {cookie_count} cookies (auth_keys_present={has_auth_cookies})")

        page = await context.new_page()

        # 选 URL
        if category == "search" and keyword:
            url = f"https://www.douyin.com/search/{keyword}?type=general"
        else:
            url = "https://www.douyin.com/hot"

        logger.info(f"Loading Douyin: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"Load failed: {e}")
        await asyncio.sleep(5)

        # 检测是否已登录（DOM 检测：有登录按钮 = 未登录）
        login_btn = await page.query_selector('text=登录')
        if not login_btn:
            login_btn = await page.query_selector('[data-e2e="login"], [class*="login-btn" i], [class*="LoginButton" i]')
        is_login_page = login_btn is not None

        if is_login_page:
            logger.warning("Not logged in — login button detected on page")

            if headless:
                logger.error("Douyin requires login. Headless mode cannot show QR code. "
                            "Please use 凭证管理 to save Douyin cookies first (浏览器登录).")
                await browser.close()
                return items

            # 非 headless: 用户可以在弹出窗口中扫码
            logger.info("═" * 50)
            logger.info("Browser window opened — please scan QR code to login (120s timeout)")
            logger.info("═" * 50)
            try:
                for _ in range(60):  # 60 * 2s = 120s
                    await asyncio.sleep(2)
                    login_btn = await page.query_selector('text=登录')
                    if not login_btn:
                        login_btn = await page.query_selector('[data-e2e="login"], [class*="login-btn" i]')
                    if login_btn is None:
                        logger.info("Login detected (login button disappeared), continuing...")
                        await asyncio.sleep(3)
                        break
                else:
                    logger.error("Login timeout (120s)")
                    await browser.close()
                    return items
            except Exception:
                logger.error("Login timeout (120s)")
                await browser.close()
                return items
        else:
            logger.info("Already logged in (no login button detected)")

        # 如果是搜索页，尝试点击筛选
        if category == "search":
            try:
                # 关闭可能的弹窗
                close_btns = await page.query_selector_all('[class*="close" i], [class*="Close" i]')
                for btn in close_btns[:3]:
                    try:
                        await btn.click(timeout=2000)
                        await asyncio.sleep(1)
                    except Exception:
                        pass

                # 点击 "筛选"
                filter_btn = page.locator('text=筛选').first
                if await filter_btn.count() > 0:
                    await filter_btn.click(timeout=5000)
                    await asyncio.sleep(2)

                    # 点 "最多点赞"
                    sort_btn = page.locator('text=最多点赞').first
                    if await sort_btn.count() > 0:
                        await sort_btn.click(timeout=5000)
                        await asyncio.sleep(3)
                        logger.info("Applied sort: 最多点赞")

                    # 再点筛选 → 选 "一周内"
                    filter_btn2 = page.locator('text=筛选').first
                    if await filter_btn2.count() > 0:
                        await filter_btn2.click(timeout=5000)
                        await asyncio.sleep(2)

                    week_btn = page.locator('text=一周内').first
                    if await week_btn.count() > 0:
                        await week_btn.click(timeout=5000)
                        await asyncio.sleep(3)
                        logger.info("Applied filter: 一周内")
            except Exception as e:
                logger.warning(f"Filter interaction failed: {e}")

        # 滚动加载
        for i in range(10):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            if i % 3 == 0:
                html = await page.content()
                ids = re.findall(r'"aweme_id"\s*:\s*"(\d+)"', html)
                logger.info(f"Scroll {i+1}: {len(set(ids))} unique aweme_ids found")

        # 从 DOM 提取视频数据
        html = await page.content()
        # 尝试匹配搜索结果中的视频卡片 JSON
        pattern = r'\{"aweme_id":"(\d+)","desc":"([^"]*)".*?"digg_count":(\d+).*?"comment_count":(\d+).*?"share_count":(\d+).*?"play_count":(\d+)'
        for m in re.finditer(pattern, html, re.DOTALL):
            aid, desc, likes, comments, shares, plays = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6))
            if aid not in {i.content_id for i in items}:
                items.append(ContentItem(
                    content_id=aid, content_type=ContentType.VIDEO,
                    title=desc, cover_url="",
                    create_time=None, duration_sec=0,
                    url=f"https://www.douyin.com/video/{aid}",
                    tags=[], music_title="",
                    likes=likes, comments=comments, shares=shares, plays=plays, collects=0,
                    extra_data={"source": category, "keyword": keyword},
                ))
                if max_items > 0 and len(items) >= max_items:
                    break

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
