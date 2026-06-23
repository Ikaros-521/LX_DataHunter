"""Bilibili platform adapter — WBI API for video list + Playwright video pages for accurate stats."""
import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime
from typing import Optional

from scrapling.fetchers import Fetcher

from backend.models import Platform, ContentType
from .base import (
    BasePlatformAdapter, ContentItem, CreatorInfo, ProgressCallback,
)

logger = logging.getLogger("fapai.adapter.bilibili")

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}

MIXIN_KEY = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
]


def _parse_cookies(cookie_str: str) -> dict:
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, _, v = item.partition("=")
            result[k.strip()] = v.strip()
    return result


def _fetch_wbi_keys(cookies: dict) -> tuple[str, str]:
    try:
        resp = Fetcher.get("https://api.bilibili.com/x/web-interface/nav",
                           headers=HEADERS, cookies=cookies if cookies else None)
        wbi = resp.json().get("data", {}).get("wbi_img", {})
        img = wbi.get("img_url", "").split("/")[-1].split(".")[0]
        sub = wbi.get("sub_url", "").split("/")[-1].split(".")[0]
        return img, sub
    except Exception as e:
        logger.warning(f"WBI keys failed: {e}")
        return "", ""


def _wbi_sign(params: dict, cookies: dict) -> dict:
    img, sub = _fetch_wbi_keys(cookies)
    if not img or not sub:
        return params
    mixin = img + sub
    mixed = "".join(mixin[i] for i in MIXIN_KEY if i < len(mixin))[:32]
    params["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((query + mixed).encode()).hexdigest()
    return params


class BilibiliAdapter(BasePlatformAdapter):
    platform = Platform.BILIBILI

    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int = 50,
        headless: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
        cookie_str: Optional[str] = None,
    ) -> tuple[CreatorInfo, list[ContentItem]]:
        mid = platform_uid.strip()
        cookies = _parse_cookies(cookie_str) if cookie_str else {}
        logger.info(f"Bilibili crawl: mid={mid}, max={max_items}, cookies={len(cookies)}")

        # ---- 阶段 1: WBI API 获取视频列表 ----
        items: list[ContentItem] = []
        page, page_size = 1, 50

        while len(items) < (max_items or 99999):
            try:
                params = _wbi_sign({"mid": mid, "ps": page_size, "pn": page, "order": "pubdate"}, cookies)
                resp = Fetcher.get(
                    "https://api.bilibili.com/x/space/wbi/arc/search",
                    params=params,
                    headers={**HEADERS, "Referer": f"https://space.bilibili.com/{mid}"},
                    cookies=cookies if cookies else None,
                )
                if resp.status != 200:
                    logger.warning(f"Search API: HTTP {resp.status}")
                    break
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning(f"Search API: code={data.get('code')}, msg={data.get('message')}")
                    break

                vlist = data.get("data", {}).get("list", {}).get("vlist", [])
                logger.info(f"Page {page}: {len(vlist)} videos")
                for v in vlist:
                    aid = str(v.get("aid", ""))
                    created = v.get("created", 0)
                    items.append(ContentItem(
                        content_id=aid,
                        content_type=ContentType.VIDEO,
                        title=v.get("title", ""),
                        cover_url=v.get("pic", ""),
                        create_time=datetime.fromtimestamp(created) if created else None,
                        duration_sec=self._parse_duration(v.get("length", "0:00")),
                        url=f"https://www.bilibili.com/video/av{aid}",
                        tags=[],
                        music_title="",
                        likes=0,
                        comments=v.get("video_review", 0),   # video_review = 评论
                        shares=0,
                        plays=v.get("play", 0),
                        collects=v.get("favorites", 0),
                        extra_data={
                            "bvid": v.get("bvid", ""),
                            "danmaku": v.get("comment", 0),   # comment = 弹幕
                            "description": v.get("description", ""),
                        },
                    ))

                if progress_callback:
                    await progress_callback(len(items), max_items, "running")

                if len(vlist) < page_size:
                    break
                page += 1
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.warning(f"Search page {page} failed: {e}")
                break

        if max_items > 0 and len(items) > max_items:
            items = items[:max_items]

        # ---- 阶段 2: HTTP API 获取用户信息 ----
        creator_info = await self._fetch_user_info(mid, cookies)

        # ---- 阶段 3: Playwright 视频页提取 stat ----
        if items:
            logger.info(f"Enriching stats for {len(items)} videos via Playwright...")
            await self._enrich_stats_with_browser(items, cookies)

        logger.info(f"Bilibili done: {creator_info.nickname}, {len(items)} videos")
        return creator_info, items

    # ---- 用户信息 ----

    async def _fetch_user_info(self, mid: str, cookies: dict) -> CreatorInfo:
        try:
            resp = Fetcher.get("https://api.bilibili.com/x/space/wbi/acc/info",
                               params=_wbi_sign({"mid": mid}, cookies),
                               headers=HEADERS, cookies=cookies if cookies else None)
            if resp.status == 200 and resp.json().get("code") == 0:
                user = resp.json()["data"]
                followers = await self._fetch_followers(mid)
                return CreatorInfo(
                    nickname=user.get("name", ""),
                    signature=user.get("sign", ""),
                    avatar_url=user.get("face", ""),
                    follower_count=followers,
                    extra_info={"mid": mid, "level": user.get("level", 0)},
                )
        except Exception as e:
            logger.warning(f"User info: {e}")
        return CreatorInfo()

    async def _fetch_followers(self, mid: str) -> int:
        try:
            resp = Fetcher.get("https://api.bilibili.com/x/relation/stat",
                               params={"vmid": mid}, headers=HEADERS)
            if resp.status == 200 and resp.json().get("code") == 0:
                return resp.json().get("data", {}).get("follower", 0)
        except Exception:
            pass
        return 0

    # ---- Playwright: 从视频页 __INITIAL_STATE__ 提取 stat ----

    @staticmethod
    def _extract_init_state_json(html: str) -> dict | None:
        """从 HTML 中提取 window.__INITIAL_STATE__ 的完整 JSON (手动数括号)"""
        # 精确匹配 "window.__INITIAL_STATE__ = {"
        marker = "window.__INITIAL_STATE__"
        search_from = 0
        while True:
            idx = html.find(marker, search_from)
            if idx == -1:
                return None

            # 跳过 " = " 或 "=" 找到 {
            after_marker = html[idx + len(marker):idx + len(marker) + 20]
            eq_pos = after_marker.find("=")
            if eq_pos == -1:
                search_from = idx + len(marker)
                continue
            brace_start = after_marker.find("{", eq_pos)
            if brace_start == -1:
                search_from = idx + len(marker)
                continue

            # 从 HTML 全局位置开始数括号
            abs_brace = idx + len(marker) + brace_start
            depth = 0
            pos = abs_brace
            while pos < len(html):
                ch = html[pos]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = html[abs_brace:pos + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            # 继续找下一个 marker
                            search_from = idx + len(marker)
                            break
                elif ch == '"':
                    pos += 1
                    while pos < len(html):
                        if html[pos] == '\\':
                            pos += 2
                            continue
                        if html[pos] == '"':
                            break
                        pos += 1
                pos += 1
            else:
                # 到 HTML 末尾未闭合
                break

            # 继续搜索下一个 marker

        logger.warning("__INITIAL_STATE__: not found or unparseable")
        return None

    async def _enrich_stats_with_browser(self, items: list[ContentItem], cookies: dict):
        from playwright.async_api import async_playwright

        chrome_exe = next((p for p in CHROME_PATHS if os.path.exists(p)), None)
        enriched = 0

        async with async_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            }
            if chrome_exe:
                launch_args["executable_path"] = chrome_exe

            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                locale="zh-CN",
                viewport={"width": 1280, "height": 720},
            )

            if cookies:
                cookie_list = [{"name": k, "value": v, "domain": ".bilibili.com", "path": "/"}
                              for k, v in cookies.items()]
                await context.add_cookies(cookie_list)

            page = await context.new_page()
            # 阻止图片/视频/字体加载，加速
            await page.route("**/*.{png,jpg,gif,svg,mp4,flv,m4s,mp3,woff2,ttf}",
                           lambda route: route.abort())

            for item in items:
                bvid = item.extra_data.get("bvid", "")
                if not bvid:
                    continue
                try:
                    await page.goto(f"https://www.bilibili.com/video/{bvid}",
                                   wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.5)

                    html = await page.content()
                    state = self._extract_init_state_json(html)
                    if not state:
                        # 诊断：检查 __INITIAL_STATE__ 是否存在
                        marker_pos = html.find("window.__INITIAL_STATE__")
                        if marker_pos == -1:
                            logger.warning(f"No __INITIAL_STATE__ in HTML for {bvid} (len={len(html)})")
                        else:
                            snippet = html[marker_pos:marker_pos + 120]
                            logger.warning(f"__INITIAL_STATE__ found but unparseable for {bvid}: snippet={snippet}")
                        continue

                    stat = state.get("videoData", {}).get("stat", {})
                    if not stat:
                        continue

                    item.likes = stat.get("like", 0)
                    item.shares = stat.get("share", 0)
                    item.collects = stat.get("favorite", 0)
                    item.plays = stat.get("view", item.plays)
                    item.comments = stat.get("reply", item.comments)
                    item.extra_data["danmaku"] = stat.get("danmaku", item.extra_data.get("danmaku", 0))
                    item.extra_data["coin"] = stat.get("coin", 0)

                    tags = state.get("videoData", {}).get("tabs", [])
                    if tags:
                        item.tags = [t.get("name", "") for t in tags if t.get("name")]

                    enriched += 1
                    if enriched % 10 == 0:
                        logger.info(f"Stats: {enriched}/{len(items)}")

                except Exception as e:
                    logger.warning(f"Stat for {bvid}: {e}")

            await browser.close()

        if enriched == 0 and len(items) > 0:
            logger.warning(f"Stats enrichment got 0/{len(items)} — check if video pages have __INITIAL_STATE__")
        else:
            logger.info(f"Stats enriched: {enriched}/{len(items)}")

    @staticmethod
    def _parse_duration(length: str) -> float:
        parts = length.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
