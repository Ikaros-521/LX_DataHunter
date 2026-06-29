"""热门内容爬取 — 抓取平台排行榜/热门内容"""
import asyncio
import logging
from datetime import datetime

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


async def _has_captcha(page) -> bool:
    """检测抖音滑块验证码 (#captcha_container) 是否可见并拦截点击。"""
    try:
        el = await page.query_selector('#captcha_container')
        if el is None:
            return False
        return await el.is_visible()
    except Exception:
        return False


async def _is_logged_in(page, context, cookie_str: str | None) -> bool:
    """可靠的登录态检测。

    不再用 'text=登录' 这种过宽的选择器（会匹配到「登录后查看」「登录抖音创作者中心」
    等无关文字，导致已登录却被误判为未登录）。改用确凿信号：
      1) 右上角 'a[href*="/user/self"]'（本人主页链接，登录后必现）
      2) 或 '退出登录' 按钮（登录后才出现）
    再用 cookie 里的登录态标识二次确认（auth_keys），避免 DOM 假阳性。
    """
    try:
        self_link = await page.query_selector('a[href*="/user/self"]')
        if self_link is None:
            logout_btn = await page.query_selector('text=退出登录')
            if logout_btn is None:
                return False
        # DOM 通过后，用登录态 cookie 二次确认
        if not cookie_str:
            return True
        auth_keys = {"sso_uid_tt", "sessionid", "sessionid_ss", "sid_guard", "uid_tt", "passport_csrf_token"}
        names = set()
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, _, _ = item.partition("=")
                names.add(k.strip())
        return bool(auth_keys & names)
    except Exception:
        return False


async def _handle_captcha(page, headless: bool) -> bool:
    """检测到验证码时处理。返回 True 表示验证码已通过(或本就没验证码)。

    - headless: 无法手动滑动，记录错误并返回 False（调用方据此次定是否继续）。
    - 非 headless: 提示用户在浏览器窗口手动滑动，轮询等待验证码消失(最多 120s)。
    """
    if not await _has_captcha(page):
        return True

    if headless:
        logger.error(
            "检测到抖音滑块验证码 (#captcha_container)。headless 模式无法手动通过，"
            "请用「凭证管理→浏览器登录」先保存抖音 Cookie，或改用非 headless 抓取。"
        )
        return False

    logger.warning("═" * 50)
    logger.warning("检测到滑块验证码 — 请在浏览器窗口手动滑动完成验证 (最多 120s)")
    logger.warning("═" * 50)
    for _ in range(60):  # 60 * 2s = 120s
        await asyncio.sleep(2)
        if not await _has_captcha(page):
            logger.info("验证码已通过，继续抓取...")
            await asyncio.sleep(2)
            return True
    logger.error("验证码等待超时 (120s)")
    return False


async def _wait_for_real_cards(page, timeout_loops: int = 15) -> bool:
    """等待搜索结果渲染出「真实」卡片（带封面图），而非骨架屏占位。

    抖音在加载/被验证码阻挡时，会先放一批空的 search-result-card 骨架。
    这些骨架没有封面图（src 不含 douyinpic）。本函数轮询直到出现带封面图
    的真实卡片，或超时。不依赖易变的 hash class。
    """
    probe = """
    () => {
        const isNum = e => /^\\d+$/.test(e.id.replace('waterfall_item_', ''));
        const cards = [...document.querySelectorAll('div[id^="waterfall_item_"]')].filter(isNum);
        let withCover = 0;
        cards.forEach(c => {
            const imgs = [...c.querySelectorAll('img')];
            if (imgs.some(i => /douyinpic/i.test(i.getAttribute('src') || ''))) withCover++;
        });
        return { total: cards.length, withCover };
    }
    """
    for _ in range(timeout_loops):
        try:
            d = await page.evaluate(probe)
            if d.get("withCover", 0) > 0:
                logger.info(f"真实结果卡片已渲染: {d['withCover']}/{d['total']} 带封面")
                return True
        except Exception:
            pass
        await asyncio.sleep(2)
    logger.warning(f"等待真实卡片超时 (仍为骨架屏)，最后状态见诊断")
    return False


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


async def _enrich_items_from_detail(page, context, items: list[ContentItem], max_items: int, headless: bool = False):
    """用视频详情页 XHR 补全抖音视频的详细互动数据。

    搜索列表页只能拿到点赞数，播放/评论/分享/收藏/时长/创建时间等需访问视频
    详情页获取。抖音详情页会请求 /aweme/v1/web/aweme/detail/ 返回完整 JSON，
    其中 aweme_detail.statistics 含 digg_count/comment_count/play_count 等，
    与 adapter._parse_xhr_data 走同一套字段。这里复用搜索阶段已打开的浏览器
    会话（同 cookie、同反检测环境），逐个访问详情页并拦截该 XHR。
    """
    if not items:
        return

    to_enrich = items[:max_items] if max_items > 0 else items
    logger.info(f"Enriching {len(to_enrich)} videos via detail-page XHR...")

    detail_url_pattern = "/aweme/v1/web/aweme/detail"
    enriched = 0

    for idx, item in enumerate(to_enrich):
        aid = item.content_id
        if not aid:
            continue

        detail_url = f"https://www.douyin.com/video/{aid}"
        captured: dict = {}

        async def on_detail_resp(response):
            try:
                if detail_url_pattern in response.url:
                    captured["data"] = await response.json()
            except Exception:
                pass

        page.on("response", on_detail_resp)
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
            # 等待 detail XHR 返回（最多 8s）；没拿到则可能是弹了验证码，处理一下再等
            for _ in range(16):
                if "data" in captured:
                    break
                await asyncio.sleep(0.5)
            if "data" not in captured and await _has_captcha(page):
                logger.warning(f"详情页 {aid} 弹出验证码，等待处理...")
                if not await _handle_captcha(page, headless):
                    logger.warning(f"详情页 {aid} 验证码未通过，跳过")
                    continue
                # 验证码通过后再等一次 XHR
                for _ in range(16):
                    if "data" in captured:
                        break
                    await asyncio.sleep(0.5)

            data = captured.get("data") or {}
            aweme = data.get("aweme_detail") or {}
            stats = aweme.get("statistics") or {}
            video = aweme.get("video") or {}
            music = aweme.get("music") or {}

            if stats:
                item.likes = stats.get("digg_count", item.likes)
                item.comments = stats.get("comment_count", 0)
                item.shares = stats.get("share_count", 0)
                item.plays = stats.get("play_count", 0)
                item.collects = stats.get("collect_count", 0)
                item.extra_data["danmaku"] = stats.get("statistic_count", 0)
            if video.get("duration"):
                item.duration_sec = round(video.get("duration", 0) / 1000, 1)
            if video.get("cover"):
                cover = video["cover"].get("url_list", [""])
                if cover and cover[0]:
                    item.cover_url = cover[0]
            ct = aweme.get("create_time", 0)
            if ct:
                item.create_time = datetime.fromtimestamp(ct)
            if music.get("title"):
                item.music_title = music.get("title", "")
            tags = [t.get("hashtag_name", "") for t in aweme.get("text_extra", [])
                    if t.get("hashtag_name")]
            if tags:
                item.tags = tags
            enriched += 1
            if enriched % 5 == 0:
                logger.info(f"Detail enrich: {enriched}/{len(to_enrich)}")
        except Exception as e:
            logger.debug(f"Detail enrich failed for {aid}: {e}")
        finally:
            page.remove_listener("response", on_detail_resp)
            await asyncio.sleep(0.3)  # 轻微限流，避免触发风控

    logger.info(f"Detail enriched: {enriched}/{len(to_enrich)}")


async def _crawl_douyin_hot(category: str, max_items: int, cookie_str: str | None, keyword: str = "") -> list[ContentItem]:
    """抖音热点/搜索 — Playwright 加载页面 + 点击筛选器"""
    import os
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

        # 反自动化检测：移除 navigator.webdriver 等 Playwright 指纹，
        # 降低抖音滑块验证码 (captcha_container) 的触发概率
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
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

        # 检测/处理滑块验证码 (#captcha_container) —— 它会拦截所有点击，
        # 导致「筛选」点不动、搜索结果不渲染。检测到时：
        #   - headless: 直接报错（无法手动滑）
        #   - 非 headless: 提示用户手动滑动后继续
        captcha_passed = await _handle_captcha(page, headless)
        if not captcha_passed:
            # headless 下验证码挡住，继续下去只会空转 10 次滚动，直接放弃
            await browser.close()
            return items

        # 检测是否已登录 —— 用可靠信号 (a[href*="/user/self"] / 退出登录 + 登录态 cookie)，
        # 不再用 'text=登录'（过宽，会误匹配「登录后查看」等文字，导致已登录被误判）。
        if await _is_logged_in(page, context, cookie_str):
            logger.info("Already logged in (user-self link / logout button detected)")
        else:
            logger.warning("Not logged in — no user-self link or auth cookies detected")

            if headless:
                logger.error("Douyin requires login. Headless mode cannot show QR code. "
                            "Please use 凭证管理 to save Douyin cookies first (浏览器登录).")
                await browser.close()
                return items

            # 非 headless: 用户可以在弹出窗口中扫码
            logger.info("═" * 50)
            logger.info("Browser window opened — please scan QR code to login (120s timeout)")
            logger.info("═" * 50)
            logged = False
            try:
                for _ in range(60):  # 60 * 2s = 120s
                    await asyncio.sleep(2)
                    if await _is_logged_in(page, context, cookie_str):
                        logger.info("Login detected, continuing...")
                        await asyncio.sleep(3)
                        logged = True
                        break
            except Exception:
                pass
            if not logged:
                logger.error("Login timeout (120s)")
                await browser.close()
                return items

        # 如果是搜索页，尝试点击筛选。
        # 注意：每次点击都可能触发滑块验证码，验证码会盖住页面、结果区停在骨架屏
        # （search-result-card 存在但无标题/封面）。所以每步点击后都要：
        #   1) 立即检测并等待验证码通过
        #   2) 等待真实卡片（带标题）渲染出来
        async def _click_and_guard(locator, label: str):
            """点击一个筛选项，随后处理验证码 + 等待真实结果。返回是否成功。"""
            if await locator.count() == 0:
                logger.info(f"筛选项 '{label}' 不存在，跳过")
                return False
            try:
                await locator.click(timeout=5000)
            except Exception as e:
                logger.warning(f"点击 '{label}' 失败: {e}")
                return False
            await asyncio.sleep(2)
            logger.info(f"Applied: {label}")
            # 点击后立即处理可能弹出的验证码
            if not await _handle_captcha(page, headless):
                logger.error(f"点击 '{label}' 后验证码未通过，放弃筛选")
                return False
            # 等待真实卡片渲染（验证码通过后结果才会真正加载）
            await _wait_for_real_cards(page)
            return True

        if category == "search":
            try:
                # 先确保初次结果区是真实的（非骨架屏），否则后续筛选点击会落空
                await _handle_captcha(page, headless)
                await _wait_for_real_cards(page)

                # 按 data-index1(行)/data-index2(列) 精确选择筛选项，
                # 抖音筛选面板用此属性标识每个选项，比 text= 更精确、不受页面其他
                # 文字干扰。text= 作为兜底。
                async def _filter_opt(idx1: int, idx2: int, label: str):
                    sel = f'[data-index1="{idx1}"][data-index2="{idx2}"]'
                    loc = page.locator(sel).first
                    if await loc.count() == 0:
                        logger.info(f"筛选项 '{label}' 无 data-index 属性，回退到 text= 选择器")
                        loc = page.locator(f'text={label}').first
                    return loc

                # 等待筛选面板展开（data-index 或筛选项文字出现）
                async def _wait_for_filter_panel(timeout_loops: int = 10):
                    for _ in range(timeout_loops):
                        try:
                            if await page.locator('[data-index1="0"]').count() > 0:
                                return True
                            # 兜底：检查常见筛选项文字是否可见
                            if await page.locator('text=综合排序').count() > 0:
                                return True
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                    logger.warning("筛选面板元素等待超时")
                    return False

                # 切换筛选面板到展开状态。
                # 注意：页面加载时面板可能已展开，"筛选"按钮是 toggle——
                # 点一次可能关闭而非打开，所以需要检测面板状态并重试。
                async def _ensure_filter_open() -> bool:
                    # 先看面板是否已经展开
                    if await _wait_for_filter_panel():
                        logger.info("筛选面板已展开，无需点击")
                        return True
                    # 面板未展开，点击"筛选"打开
                    filter_btn = page.locator('text=筛选').first
                    if await filter_btn.count() == 0:
                        logger.warning("未找到「筛选」按钮")
                        return False
                    try:
                        await filter_btn.click(timeout=5000)
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.warning(f"点击「筛选」失败: {e}")
                        return False
                    if await _wait_for_filter_panel():
                        logger.info("点击筛选后面板已展开")
                        return True
                    # 可能面板原本是展开的，点击后关掉了——再点一次
                    logger.info("点击筛选后面板未展开（可能原本是开着的），再点一次")
                    try:
                        await filter_btn.click(timeout=5000)
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.warning(f"第二次点击「筛选」失败: {e}")
                        return False
                    return await _wait_for_filter_panel()

                if await _ensure_filter_open():
                    # 排序依据行(data-index1=0): 综合排序(0), 最新发布(1), 最多点赞(2)
                    opt = await _filter_opt(0, 2, "最多点赞")
                    if await _click_and_guard(opt, "最多点赞"):
                        pass
                    # 再展开筛选面板 → 选 "一周内"
                    if await _ensure_filter_open():
                        # 发布时间行(data-index1=1): 不限(0), 一天内(1), 一周内(2), 半年内(3)
                        opt = await _filter_opt(1, 2, "一周内")
                        await _click_and_guard(opt, "一周内")
            except Exception as e:
                logger.warning(f"Filter interaction failed: {e}")

            # 最后兜底：再检测一次验证码 + 等真实卡片
            await _handle_captcha(page, headless)
            await _wait_for_real_cards(page)

        # 浏览器内 JS：从每个视频卡片 DOM 提取数据。
        # 关键设计：不依赖抖音的 hash class（会随改版/AB测试变化，如 .DghJW248
        # 已失效），改用「结构 + 内容特征」定位，跨改版更稳定：
        #   - 卡片：div[id^="waterfall_item_"] 且 id 为纯数字（aweme_id）
        #   - 封面：卡片内 src 含 douyinpic 的 <img>
        #   - 时长：卡片内匹配 MM:SS / H:MM:SS 的文本
        #   - 点赞：卡片内匹配「数字(+万/亿)」的文本
        #   - 标题：卡片内最长的文本块
        extract_js = """
        () => {
            const num = (s) => {
                if (!s) return 0;
                s = String(s).trim().replace(/[,，\\s]/g, '');
                let m = s.match(/^([\\d.]+)\\s*([万亿wWkK]*)/);
                if (!m) return 0;
                let v = parseFloat(m[1]); const u = m[2];
                if (u.startsWith('万') || u.toLowerCase().startsWith('w')) v *= 10000;
                else if (u.startsWith('亿')) v *= 100000000;
                else if (u.toLowerCase().startsWith('k')) v *= 1000;
                return Math.round(v);
            };
            const durRe = /^\\d{1,2}:\\d{2}(:\\d{2})?$/;           // 00:52 / 1:02:38
            const likeRe = /^[\\d.]+\\s*[万亿wkWK]?$/;             // 52 / 1.4万 / 78.8万
            const out = [];
            const cards = document.querySelectorAll('div[id^="waterfall_item_"]');
            cards.forEach(card => {
                const id = (card.id || '').replace('waterfall_item_', '');
                if (!/^\\d+$/.test(id)) return;                    // 跳过「相关搜索」等非视频卡片

                // 封面：src 含 douyinpic 的图片（封面图固定走该 CDN）
                const coverImg = [...card.querySelectorAll('img')]
                    .find(i => /douyinpic/i.test(i.getAttribute('src') || ''));
                const cover = coverImg ? coverImg.getAttribute('src') : '';

                // 时长 / 点赞：扫描所有叶子文本节点
                let dur = '', likes = 0;
                const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = (node.textContent || '').trim();
                    if (!t) continue;
                    if (!dur && durRe.test(t)) dur = t;
                    if (likeRe.test(t)) {
                        const v = num(t);
                        if (v > likes) likes = v;                 // 取数值最大的（点赞数 > 时长数字）
                    }
                }

                // 标题：卡片内最长的纯文本块（视频描述通常最长）
                let title = '';
                card.querySelectorAll('div, p, span').forEach(el => {
                    // 只取「自身直接文本」，不含子元素文本，避免取到容器
                    const direct = [...el.childNodes]
                        .filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
                    if (direct.length > title.length) title = direct;
                });

                // 作者：@ 开头的文本，或带 @ 标记的 span
                let author = '';
                const atNode = [...card.querySelectorAll('*')]
                    .find(e => /^@.+/.test((e.textContent || '').trim()));
                if (atNode) author = atNode.textContent.trim().replace(/^@/, '');

                // 只要拿到 aweme_id 就算有效卡片（标题/封面可能因加载延迟暂时缺失）
                out.push({ id, title, cover, dur, likes, author });
            });
            return out;
        }
        """

        # 诊断 JS：dump 第一个真实卡片的完整 outerHTML，用于核对选择器/定位改版
        diag_js = """
        () => {
            const num = e => /^\\d+$/.test(e.id.replace('waterfall_item_', ''));
            const first = [...document.querySelectorAll('div[id^="waterfall_item_"]')].find(num);
            return {
                url: location.href,
                wfItemCount: document.querySelectorAll('[id^="waterfall_item_"]').length,
                wfNumericCount: [...document.querySelectorAll('[id^="waterfall_item_"]')].filter(num).length,
                hasCaptcha: !!document.querySelector('#captcha_container'),
                firstCardHTML: first ? first.outerHTML.slice(0, 3000) : '(no numeric card)',
            }
        }
        """

        async def _dump_diag(tag: str):
            try:
                d = await page.evaluate(diag_js)
                logger.warning(
                    f"[DIAG {tag}] url={d.get('url')} wfItem={d.get('wfItemCount')} "
                    f"wfNumeric={d.get('wfNumericCount')} captcha={d.get('hasCaptcha')}"
                )
                logger.warning(f"[DIAG {tag}] firstCardHTML: {d.get('firstCardHTML')}")
            except Exception as e:
                logger.warning(f"[DIAG {tag}] failed: {e}")

        await _dump_diag("before-scroll")

        # 滚动加载，边滚边累计已见卡片（用集合去重）
        seen_ids: set[str] = set()
        for i in range(10):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            try:
                cards = await page.evaluate(extract_js)
                new_ids = {c["id"] for c in cards} - seen_ids
                seen_ids.update(c["id"] for c in cards)
                logger.info(f"Scroll {i+1}/10: {len(cards)} cards total, {len(new_ids)} new, {len(seen_ids)} unique")
                # 首次发现 0 卡片时 dump 一次诊断，定位是选择器问题还是页面真空
                if len(cards) == 0 and i == 0:
                    await _dump_diag("scroll1-empty")
            except Exception as e:
                logger.warning(f"Scroll {i+1}: extract failed: {e}")

        # 最终提取一次（滚动结束后 DOM 已稳定）
        try:
            cards = await page.evaluate(extract_js)
        except Exception as e:
            logger.error(f"Final DOM extract failed: {e}")
            cards = []

        for c in cards:
            aid = c.get("id", "")
            if not aid or aid in {it.content_id for it in items}:
                continue
            items.append(ContentItem(
                content_id=aid,
                content_type=ContentType.VIDEO,
                title=c.get("title", ""),
                cover_url=c.get("cover", ""),
                create_time=None,
                duration_sec=_parse_duration(c.get("dur", "0")),
                url=f"https://www.douyin.com/video/{aid}",
                tags=[],
                music_title="",
                likes=int(c.get("likes", 0)),
                comments=0,
                shares=0,
                plays=0,
                collects=0,
                extra_data={
                    "source": category,
                    "keyword": keyword,
                    "author": c.get("author", ""),
                },
            ))
            if max_items > 0 and len(items) >= max_items:
                break

        # 用「项目的另一种抓取方式」（XHR 拦截）补全每个视频的详细数据。
        # 搜索列表页只能拿到点赞数，播放/评论/分享/收藏等需访问视频详情页，
        # 抖音详情页会请求 /aweme/v1/web/aweme/detail/ 返回完整 statistics，
        # 复用浏览器会话（同 cookie/反检测环境）逐个抓取。与 adapter 的
        # _parse_xhr_data 走同一套字段（digg_count/comment_count/play_count 等）。
        await _enrich_items_from_detail(page, context, items, max_items, headless)

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
