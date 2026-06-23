"""Bilibili platform adapter — Playwright renders pages, extracts __INITIAL_STATE__."""
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

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
        """Playwright 浏览器抓取：space 页提取视频列表 + 逐个视频页提取 stat"""
        from playwright.async_api import async_playwright

        mid = platform_uid.strip()
        space_url = f"https://space.bilibili.com/{mid}/video"
        logger.info(f"Bilibili crawl: mid={mid}, max={max_items}")

        chrome_exe = next((p for p in CHROME_PATHS if os.path.exists(p)), None)
        items: list[ContentItem] = []
        creator_info = CreatorInfo()

        async with async_playwright() as p:
            launch_args = {
                "headless": headless,
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            }
            if chrome_exe:
                launch_args["executable_path"] = chrome_exe

            browser = await p.chromium.launch(**launch_args)
            context_kwargs = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "locale": "zh-CN",
                "viewport": {"width": 1280, "height": 800},
            }
            context = await browser.new_context(**context_kwargs)

            # 注入 Cookie
            if cookie_str:
                cookie_list = []
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, _, v = item.partition("=")
                        cookie_list.append({"name": k.strip(), "value": v.strip(), "domain": ".bilibili.com", "path": "/"})
                if cookie_list:
                    await context.add_cookies(cookie_list)
                    logger.info(f"Injected {len(cookie_list)} cookies")

            page = await context.new_page()

            # ---- 阶段 1: 加载 space 页面，提取视频列表 ----
            logger.info(f"Loading space page: {space_url}")
            try:
                await page.goto(space_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"Space page load: {e}")
            await asyncio.sleep(3)

            html = await page.content()
            state = self._extract_init_state(html)
            if state:
                items = self._parse_space_state(state, max_items)
                logger.info(f"Extracted {len(items)} videos from __INITIAL_STATE__")

            # 如果不够，滚动触发更多
            if len(items) < max_items:
                # 拦截搜索 API
                async def on_resp(response):
                    url = response.url
                    if "/x/space/wbi/arc/search" in url or "/x/space/arc/search" in url:
                        try:
                            data = await response.json()
                            if data.get("code") == 0:
                                new = self._parse_search_api(data, max_items - len(items))
                                for n in new:
                                    if n.content_id not in {i.content_id for i in items}:
                                        items.append(n)
                                total = len(items)
                                logger.info(f"Search API: +{len(new)} videos, total={total}")
                                if progress_callback:
                                    await progress_callback(total, max_items, "running")
                        except Exception:
                            pass

                page.on("response", on_resp)

                prev = len(items)
                no_new = 0
                for i in range(15):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)
                    if len(items) >= max_items:
                        break
                    if len(items) == prev:
                        no_new += 1
                        if no_new >= 4:
                            break
                    else:
                        no_new = 0
                    prev = len(items)
                    if i % 3 == 0:
                        logger.info(f"Scroll {i+1}: {len(items)} videos")

            if max_items > 0 and len(items) > max_items:
                items = items[:max_items]

            # ---- 阶段 2: 提取作者信息 ----
            creator_info = await self._extract_creator_from_page(page, mid)

            # ---- 阶段 3: 逐视频页提取 stat ----
            if items:
                logger.info(f"Enriching stats for {len(items)} videos via video pages...")
                await self._enrich_stats(page, items)

            await browser.close()

        logger.info(f"Bilibili done: {creator_info.nickname}, {len(items)} videos")
        return creator_info, items

    # ---- __INITIAL_STATE__ 解析 ----

    @staticmethod
    def _extract_init_state(html: str) -> dict | None:
        """从 HTML 提取 window.__INITIAL_STATE__"""
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*\(function', html, re.DOTALL)
        if not match:
            match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _parse_space_state(self, state: dict, max_items: int) -> list[ContentItem]:
        """从 space 页面的 __INITIAL_STATE__ 提取视频列表"""
        items = []
        # 可能的位置: state.data.list.vlist 或 state.videoList 等
        video_list = (
            state.get("data", {}).get("list", {}).get("vlist", []) or
            state.get("videoList", []) or
            []
        )
        for v in video_list:
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
                comments=v.get("video_review", 0),
                shares=0,
                plays=v.get("play", 0),
                collects=v.get("favorites", 0),
                extra_data={
                    "bvid": v.get("bvid", ""),
                    "danmaku": v.get("comment", 0),
                    "description": v.get("description", ""),
                },
            ))
            if max_items > 0 and len(items) >= max_items:
                break
        return items

    def _parse_search_api(self, data: dict, remaining: int) -> list[ContentItem]:
        """从搜索 API JSON 提取视频列表"""
        items = []
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
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
                comments=v.get("video_review", 0),
                shares=0,
                plays=v.get("play", 0),
                collects=v.get("favorites", 0),
                extra_data={
                    "bvid": v.get("bvid", ""),
                    "danmaku": v.get("comment", 0),
                    "description": v.get("description", ""),
                },
            ))
            if remaining > 0 and len(items) >= remaining:
                break
        return items

    # ---- 作者信息 ----

    async def _extract_creator_from_page(self, page, mid: str) -> CreatorInfo:
        """从 space 页面 DOM 提取作者信息"""
        try:
            html = await page.content()
            state = self._extract_init_state(html)
            if state:
                # 尝试从 space 页面的 state 提取
                user_info = (
                    state.get("data", {}).get("card", {}) or
                    state.get("userInfo", {}) or
                    {}
                )
                if user_info.get("name"):
                    return CreatorInfo(
                        nickname=user_info.get("name", ""),
                        signature=user_info.get("sign", ""),
                        avatar_url=user_info.get("face", ""),
                        follower_count=user_info.get("fans", 0),
                        extra_info={"mid": mid, "level": user_info.get("level", 0)},
                    )
        except Exception:
            pass

        # 回退：HTTP API
        return await self._fetch_user_info_http(mid)

    async def _fetch_user_info_http(self, mid: str) -> CreatorInfo:
        """HTTP API 获取用户信息（备用）"""
        from scrapling.fetchers import Fetcher
        try:
            resp = Fetcher.get(
                "https://api.bilibili.com/x/space/wbi/acc/info",
                params={"mid": mid},
                headers=HEADERS,
            )
            if resp.status == 200 and resp.json().get("code") == 0:
                user = resp.json()["data"]
                followers = 0
                try:
                    resp2 = Fetcher.get("https://api.bilibili.com/x/relation/stat", params={"vmid": mid}, headers=HEADERS)
                    if resp2.status == 200:
                        followers = resp2.json().get("data", {}).get("follower", 0)
                except Exception:
                    pass
                return CreatorInfo(
                    nickname=user.get("name", ""),
                    signature=user.get("sign", ""),
                    avatar_url=user.get("face", ""),
                    follower_count=followers,
                    extra_info={"mid": mid, "level": user.get("level", 0)},
                )
        except Exception as e:
            logger.warning(f"User info HTTP failed: {e}")
        return CreatorInfo()

    # ---- 视频页 stat ----

    async def _enrich_stats(self, page, items: list[ContentItem]):
        """在已打开的浏览器中逐个加载视频页，从 __INITIAL_STATE__ 提取 stat"""
        enriched = 0
        for item in items:
            bvid = item.extra_data.get("bvid", "")
            if not bvid:
                continue
            try:
                url = f"https://www.bilibili.com/video/{bvid}"
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1.5)

                html = await page.content()
                state = self._extract_init_state(html)
                if not state:
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

        logger.info(f"Stats enriched: {enriched}/{len(items)}")

    # ---- 工具 ----

    @staticmethod
    def _parse_duration(length: str) -> float:
        parts = length.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
