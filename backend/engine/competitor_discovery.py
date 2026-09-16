"""关键词发现竞品账号（支持多页翻页聚合）。

- B站：bilibili-api search 模块搜视频（纯 API 免登录，page 翻页，
  每页 42 条），按作者 mid 聚合 + relation/stat 补粉丝数
- 抖音：Playwright 打开搜索页（需登录 Cookie），滚动触发分页，
  拦截 /aweme/v1/web/search/item/ XHR 提取视频作者，按 sec_uid 聚合

聚合维度：搜索命中数 / 最新发布时间 / 命中视频播放量合计，
供前端排序筛选。

抖音路径在 Windows + uvicorn 下需由调用方放入独立线程事件循环执行
（同 hot_crawler._run_coro_in_thread 模式）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from .comment_crawler import CHROME_PATHS, DOUYIN_UA

logger = logging.getLogger("lx_datahunter.discovery")


@dataclass
class DiscoveredAccount:
    """关键词搜索聚合出的候选竞品账号。"""
    platform: str
    platform_uid: str
    nickname: str = ""
    signature: str = ""
    follower_count: int = 0
    video_hits: int = 0                       # 搜索结果中出现次数
    sample_titles: list[str] = field(default_factory=list)
    homepage_url: str = ""
    latest_pub_time: str = ""                 # 命中视频的最新发布日期 YYYY-MM-DD
    total_plays: int = 0                      # 命中视频播放量合计（B站直接有；抖音无则 0）
    _latest_pub_ts: int = 0                   # 内部：最新发布时间戳（聚合用，不序列化）


def _merge_hits(accounts: dict[str, DiscoveredAccount], limit: int) -> list[DiscoveredAccount]:
    """按出现次数降序（次按命中播放量）取前 limit 个候选。"""
    items = sorted(
        accounts.values(),
        key=lambda a: (a.video_hits, a.total_plays),
        reverse=True,
    )
    return items[:limit]


def _apply_hit(acc: DiscoveredAccount, pub_ts: int, plays: int, title: str,
               max_samples: int = 3):
    """把一次搜索命中并入账号聚合。"""
    acc.video_hits += 1
    acc.total_plays += max(plays, 0)
    if pub_ts > acc._latest_pub_ts:
        acc._latest_pub_ts = pub_ts
        acc.latest_pub_time = datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d") if pub_ts else ""
    if title and len(acc.sample_titles) < max_samples:
        acc.sample_titles.append(title)


# --------------------------------------------------------------------------
# B站（纯 API，page 翻页）
# --------------------------------------------------------------------------

async def discover_bilibili(keyword: str, max_items: int = 20,
                            max_pages: int = 3) -> list[DiscoveredAccount]:
    """B站关键词搜视频（翻 max_pages 页，每页 42 条）→ 按作者聚合。"""
    from bilibili_api import search as bili_search, sync
    from bilibili_api.search import SearchObjectType

    accounts: dict[str, DiscoveredAccount] = {}
    total_videos = 0
    for page in range(1, max_pages + 1):
        try:
            data = sync(bili_search.search_by_type(
                keyword, search_type=SearchObjectType.VIDEO, page=page,
            ))
        except Exception as e:
            logger.warning(f"Bilibili search page {page} failed for '{keyword}': {e}")
            break

        results = (data or {}).get("result") or []
        if not results:
            logger.info(f"Bilibili search page {page}: empty, stop paging")
            break

        for v in results:
            if not isinstance(v, dict):
                continue
            mid = str(v.get("mid", "") or "")
            if not mid:
                continue
            title = (v.get("title") or "").replace('<em class="keyword">', '').replace("</em>", "")
            if mid not in accounts:
                accounts[mid] = DiscoveredAccount(
                    platform="bilibili",
                    platform_uid=mid,
                    nickname=v.get("author", "") or "",
                    homepage_url=f"https://space.bilibili.com/{mid}",
                )
            _apply_hit(accounts[mid],
                       pub_ts=int(v.get("pubdate", 0) or 0),
                       plays=int(v.get("play", 0) or 0),
                       title=title)
        total_videos += len(results)

        # 候选足够多时提前停止翻页
        if len(accounts) >= max_items * 2:
            break
        if page < max_pages:
            await asyncio.sleep(1.2)  # 翻页间隔，降低风控概率

    candidates = _merge_hits(accounts, max_items)
    await _fill_bilibili_followers(candidates)
    logger.info(f"Bilibili discovery '{keyword}': {total_videos} videos "
                f"({len(accounts)} accounts) -> top {len(candidates)}")
    return candidates


async def _fill_bilibili_followers(candidates: list[DiscoveredAccount]):
    """搜索结果不含粉丝数，用 x/relation/stat 逐个补齐（失败不影响结果）。"""
    import httpx

    async with httpx.AsyncClient(timeout=15,
                                 headers={"User-Agent": "Mozilla/5.0"}) as client:
        async def fetch_one(acc: DiscoveredAccount):
            try:
                r = await client.get("https://api.bilibili.com/x/relation/stat",
                                     params={"vmid": acc.platform_uid})
                acc.follower_count = (r.json().get("data") or {}).get("follower", 0)
            except Exception:
                pass

        await asyncio.gather(*(fetch_one(c) for c in candidates))


# --------------------------------------------------------------------------
# 抖音（Playwright + XHR 拦截，需登录；滚动即翻页）
# --------------------------------------------------------------------------

def parse_douyin_search_payload(data: dict) -> list[dict]:
    """解析抖音搜索 XHR JSON，返回 [{sec_uid, nickname, signature,
    follower_count, desc, create_time, plays}]（纯函数，便于单测）。"""
    out: list[dict] = []
    for block in data.get("data") or []:
        if not isinstance(block, dict):
            continue
        for aweme in block.get("aweme_list") or []:
            info = aweme.get("aweme_info") or aweme  # 不同版本字段层级不同
            if not isinstance(info, dict):
                continue
            author = info.get("author") or {}
            sec_uid = author.get("sec_uid", "") or ""
            if not sec_uid:
                continue
            stats = info.get("statistics") or {}
            out.append({
                "sec_uid": sec_uid,
                "nickname": author.get("nickname", "") or "",
                "signature": author.get("signature", "") or "",
                "follower_count": author.get("follower_count", 0) or 0,
                "desc": info.get("desc", "") or "",
                "create_time": int(info.get("create_time", 0) or 0),
                "plays": int(stats.get("play_count", 0) or 0),
            })
    return out


async def discover_douyin(
    keyword: str,
    cookie_str: str | None = None,
    max_items: int = 20,
    max_pages: int = 3,
    headless: bool = True,
) -> list[DiscoveredAccount]:
    """抖音关键词搜视频 → 拦截搜索 XHR → 按作者聚合。

    翻页深度 max_pages 控制滚动批数（每批约 10-15 条结果）。
    抖音搜索强制登录：无有效 Cookie 且 headless 时返回空列表并提示
    （调用方降级）；非 headless 时允许用户扫码。
    """
    from playwright.async_api import async_playwright
    from .hot_crawler import _handle_captcha, _is_logged_in

    accounts: dict[str, DiscoveredAccount] = {}
    url = f"https://www.douyin.com/search/{keyword}?type=general"
    scroll_rounds = min(max_pages * 3, 12)  # 每页约 3 次滚动

    async with async_playwright() as p:
        launch_args: dict = {"headless": headless, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        chrome_exe = next((cp for cp in CHROME_PATHS if os.path.exists(cp)), None)
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=DOUYIN_UA, locale="zh-CN",
            viewport={"width": 1280, "height": 800},
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
        xhr_data: list[dict] = []

        async def on_response(response):
            if "/aweme/v1/web/search/item" in response.url:
                try:
                    xhr_data.append(await response.json())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"Douyin search page load incomplete: {e}")
            await asyncio.sleep(5)

            if not await _handle_captcha(page, headless):
                logger.warning("Douyin discovery aborted: captcha blocked")
                await browser.close()
                return []

            if not await _is_logged_in(page, context, cookie_str):
                if headless:
                    logger.warning(
                        "Douyin discovery needs login — save Douyin cookies in "
                        "凭证管理 first, or retry with headless=False"
                    )
                    await browser.close()
                    return []
                logger.info("Waiting up to 120s for QR login...")
                logged = False
                for _ in range(60):
                    await asyncio.sleep(2)
                    if await _is_logged_in(page, context, cookie_str):
                        logged = True
                        break
                if not logged:
                    await browser.close()
                    return []
                await asyncio.sleep(3)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                await asyncio.sleep(5)

            # 滚动加载更多搜索结果（即翻页）
            target_hits = max_items * 2
            for _ in range(scroll_rounds):
                if sum(len(parse_douyin_search_payload(d)) for d in xhr_data) >= target_hits:
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
        finally:
            await browser.close()

    for d in xhr_data:
        for hit in parse_douyin_search_payload(d):
            sec_uid = hit["sec_uid"]
            if sec_uid not in accounts:
                accounts[sec_uid] = DiscoveredAccount(
                    platform="douyin",
                    platform_uid=sec_uid,
                    nickname=hit["nickname"],
                    signature=hit["signature"],
                    follower_count=hit["follower_count"],
                    homepage_url=f"https://www.douyin.com/user/{sec_uid}",
                )
            acc = accounts[sec_uid]
            if hit["follower_count"] > acc.follower_count:
                acc.follower_count = hit["follower_count"]
            _apply_hit(acc, pub_ts=hit["create_time"], plays=hit["plays"],
                       title=hit["desc"])

    candidates = _merge_hits(accounts, max_items)
    logger.info(f"Douyin discovery '{keyword}': {len(candidates)} accounts")
    return candidates
