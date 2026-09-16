"""关键词发现竞品账号。

- B站：bilibili-api search 模块搜视频（纯 API 免登录），按作者 mid 聚合
- 抖音：Playwright 打开搜索页（需登录 Cookie），拦截 /aweme/v1/web/search/item/
  XHR 提取视频作者（sec_uid/昵称/签名/粉丝数），按 sec_uid 聚合

抖音路径在 Windows + uvicorn 下需由调用方放入独立线程事件循环执行
（同 hot_crawler._run_coro_in_thread 模式）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

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


def _merge_hits(accounts: dict[str, DiscoveredAccount], limit: int) -> list[DiscoveredAccount]:
    """按出现次数降序取前 limit 个候选。"""
    items = sorted(accounts.values(), key=lambda a: a.video_hits, reverse=True)
    return items[:limit]


# --------------------------------------------------------------------------
# B站（纯 API）
# --------------------------------------------------------------------------

async def discover_bilibili(keyword: str, max_items: int = 20) -> list[DiscoveredAccount]:
    """B站关键词搜视频 → 按作者聚合出候选竞品账号（并补齐粉丝数）。"""
    from bilibili_api import search as bili_search, sync
    from bilibili_api.search import SearchObjectType

    accounts: dict[str, DiscoveredAccount] = {}
    try:
        data = sync(bili_search.search_by_type(
            keyword, search_type=SearchObjectType.VIDEO,
        ))
    except Exception as e:
        logger.warning(f"Bilibili search failed for '{keyword}': {e}")
        return []

    results = (data or {}).get("result") or []
    for v in results[:max_items * 3]:  # 多取一些原材料用于聚合
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
        acc = accounts[mid]
        acc.video_hits += 1
        if len(acc.sample_titles) < 3 and title:
            acc.sample_titles.append(title)

    candidates = _merge_hits(accounts, max_items)
    await _fill_bilibili_followers(candidates)
    logger.info(f"Bilibili discovery '{keyword}': {len(results)} videos -> {len(candidates)} accounts")
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
# 抖音（Playwright + XHR 拦截，需登录）
# --------------------------------------------------------------------------

def parse_douyin_search_payload(data: dict) -> list[dict]:
    """解析抖音搜索 XHR JSON，返回 [{sec_uid, nickname, signature,
    follower_count, desc}]（纯函数，便于单测）。"""
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
            out.append({
                "sec_uid": sec_uid,
                "nickname": author.get("nickname", "") or "",
                "signature": author.get("signature", "") or "",
                "follower_count": author.get("follower_count", 0) or 0,
                "desc": info.get("desc", "") or "",
            })
    return out


async def discover_douyin(
    keyword: str,
    cookie_str: str | None = None,
    max_items: int = 20,
    headless: bool = True,
) -> list[DiscoveredAccount]:
    """抖音关键词搜视频 → 拦截搜索 XHR → 按作者聚合。

    抖音搜索强制登录：无有效 Cookie 且 headless 时返回空列表并提示
    （调用方降级）；非 headless 时允许用户扫码。
    """
    from playwright.async_api import async_playwright
    from .hot_crawler import _handle_captcha, _is_logged_in

    accounts: dict[str, DiscoveredAccount] = {}
    url = f"https://www.douyin.com/search/{keyword}?type=general"

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

            # 滚动加载更多搜索结果
            for _ in range(5):
                if sum(len(parse_douyin_search_payload(d)) for d in xhr_data) >= max_items * 2:
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
            acc.video_hits += 1
            if len(acc.sample_titles) < 3 and hit["desc"]:
                acc.sample_titles.append(hit["desc"])

    candidates = _merge_hits(accounts, max_items)
    logger.info(f"Douyin discovery '{keyword}': {len(candidates)} accounts")
    return candidates
