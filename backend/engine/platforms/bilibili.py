"""Bilibili platform adapter — uses Playwright to render space page and intercept API."""
import asyncio
import logging
import os
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
        """通过 Playwright 浏览器抓取 B站用户空间视频。platform_uid = 用户 mid (数字)"""
        from playwright.async_api import async_playwright

        mid = platform_uid.strip()
        space_url = f"https://space.bilibili.com/{mid}/video"
        logger.info(f"Bilibili crawl starting: mid={mid}, max_items={max_items}, has_cookie={bool(cookie_str)}")

        xhr_data: list[dict] = []
        creator_info = CreatorInfo()

        chrome_exe = next((p for p in CHROME_PATHS if os.path.exists(p)), None)
        if chrome_exe:
            logger.info(f"Using Chrome: {chrome_exe}")

        async with async_playwright() as p:
            launch_args = {
                "headless": headless,
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            }
            if chrome_exe:
                launch_args["executable_path"] = chrome_exe

            browser = await p.chromium.launch(**launch_args)

            # 创建上下文 — 如果有 Cookie 则注入
            context_kwargs: dict = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "locale": "zh-CN",
                "viewport": {"width": 1920, "height": 1080},
            }
            context = await browser.new_context(**context_kwargs)

            # 注入 Cookie
            if cookie_str:
                cookies_to_set = []
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, _, v = item.partition("=")
                        cookies_to_set.append({
                            "name": k.strip(), "value": v.strip(),
                            "domain": ".bilibili.com", "path": "/",
                        })
                if cookies_to_set:
                    await context.add_cookies(cookies_to_set)
                    logger.info(f"Injected {len(cookies_to_set)} cookies")

            page = await context.new_page()

            # 拦截搜索 API 响应
            async def on_response(response):
                url = response.url
                # B站空间视频列表 API
                if "/x/space/wbi/arc/search" in url or "/x/space/arc/search" in url:
                    try:
                        data = await response.json()
                        if data.get("code") == 0:
                            xhr_data.append(data)
                            total = sum(
                                len(d.get("data", {}).get("list", {}).get("vlist", []))
                                for d in xhr_data
                            )
                            logger.info(f"Captured search API: {total} videos so far")
                            if progress_callback:
                                await progress_callback(total, max_items, "running")
                    except Exception:
                        pass

            page.on("response", on_response)

            # 导航到空间视频页
            logger.info(f"Navigating to: {space_url}")
            try:
                await page.goto(space_url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                logger.warning(f"Navigation incomplete: {e}")
            await asyncio.sleep(3)

            # 滚动加载更多
            logger.info("Scrolling to load more videos...")
            prev_total = 0
            no_new = 0
            for i in range(20):
                current_total = sum(
                    len(d.get("data", {}).get("list", {}).get("vlist", []))
                    for d in xhr_data
                )
                if max_items > 0 and current_total >= max_items:
                    logger.info(f"Reached max_items={max_items}")
                    break
                if current_total == prev_total:
                    no_new += 1
                    if no_new >= 5:
                        break
                else:
                    no_new = 0
                prev_total = current_total
                if i % 3 == 0:
                    logger.info(f"Scroll {i+1}/20: {current_total} videos so far")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

            await browser.close()

        # 解析视频列表
        items = self._parse_xhr_data(xhr_data, max_items)

        # 提取作者信息
        for d in xhr_data:
            vlist = d.get("data", {}).get("list", {}).get("vlist", [])
            if vlist:
                # 从页面数据提取（如果有），否则从第一个视频推测
                pass
            break

        # 用第一个视频的信息 + 单独的 API 获取用户信息
        creator_info = await self._fetch_creator_info(mid)

        # 批量获取准确互动数据
        if items:
            logger.info(f"Fetching detailed stats for {len(items)} videos...")
            await self._enrich_with_stats(items)

        logger.info(
            f"Bilibili crawl done: mid={mid}, nickname='{creator_info.nickname}', "
            f"total_items={len(items)}"
        )
        return creator_info, items

    def _parse_xhr_data(self, xhr_data: list[dict], max_items: int) -> list[ContentItem]:
        """解析拦截到的搜索 API JSON"""
        items: list[ContentItem] = []
        seen = set()
        for data in xhr_data:
            vlist = data.get("data", {}).get("list", {}).get("vlist", [])
            for v in vlist:
                aid = str(v.get("aid", ""))
                if aid in seen:
                    continue
                seen.add(aid)

                created = v.get("created", 0)
                create_dt = datetime.fromtimestamp(created) if created else None
                length_str = v.get("length", "0:00")
                duration = self._parse_duration(length_str)

                # 搜索 API 字段：
                #   comment = 弹幕, video_review = 评论, play = 播放
                items.append(ContentItem(
                    content_id=aid,
                    content_type=ContentType.VIDEO,
                    title=v.get("title", ""),
                    cover_url=v.get("pic", ""),
                    create_time=create_dt,
                    duration_sec=duration,
                    url=f"https://www.bilibili.com/video/av{aid}" if aid else "",
                    tags=[],
                    music_title="",
                    likes=0,  # 下面批量获取
                    comments=v.get("video_review", 0),
                    shares=0,  # 下面批量获取
                    plays=v.get("play", 0),
                    collects=v.get("favorites", 0),
                    extra_data={
                        "bvid": v.get("bvid", ""),
                        "aid": aid,
                        "danmaku": v.get("comment", 0),
                        "description": v.get("description", ""),
                    },
                ))
                if max_items > 0 and len(items) >= max_items:
                    return items
        return items

    async def _fetch_creator_info(self, mid: str) -> CreatorInfo:
        """获取用户基本信息"""
        from scrapling.fetchers import Fetcher

        try:
            resp = Fetcher.get(
                "https://api.bilibili.com/x/space/wbi/acc/info",
                params={"mid": mid},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": f"https://space.bilibili.com/{mid}",
                },
            )
            if resp.status == 200 and resp.json().get("code") == 0:
                user = resp.json()["data"]
                # 粉丝数
                follower_count = await self._fetch_follower_count(mid)
                return CreatorInfo(
                    nickname=user.get("name", ""),
                    signature=user.get("sign", ""),
                    avatar_url=user.get("face", ""),
                    follower_count=follower_count,
                    extra_info={
                        "mid": mid, "level": user.get("level", 0),
                        "sex": user.get("sex", ""),
                    },
                )
        except Exception as e:
            logger.warning(f"Failed to fetch creator info via HTTP: {e}")

        return CreatorInfo()

    async def _fetch_follower_count(self, mid: str) -> int:
        """获取粉丝数"""
        from scrapling.fetchers import Fetcher
        try:
            resp = Fetcher.get(
                "https://api.bilibili.com/x/relation/stat",
                params={"vmid": mid},
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": f"https://space.bilibili.com/{mid}",
                },
            )
            if resp.status == 200 and resp.json().get("code") == 0:
                return resp.json().get("data", {}).get("follower", 0)
        except Exception as e:
            logger.warning(f"Failed to fetch follower count: {e}")
        return 0

    async def _enrich_with_stats(self, items: list[ContentItem]):
        """逐个获取视频详细互动数据（view API 无需签名，数据最准确）"""
        from scrapling.fetchers import Fetcher

        enriched = 0
        for item in items:
            try:
                resp = Fetcher.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"aid": item.content_id},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Referer": "https://www.bilibili.com/",
                    },
                )
                if resp.status != 200:
                    continue
                data = resp.json()
                if data.get("code") != 0:
                    continue

                stat = data.get("data", {}).get("stat", {})
                if stat:
                    item.likes = stat.get("like", 0)
                    item.shares = stat.get("share", 0)
                    item.collects = stat.get("favorite", 0)
                    item.plays = stat.get("view", item.plays)
                    item.comments = stat.get("reply", item.comments)
                    item.extra_data["danmaku"] = stat.get("danmaku", 0)
                    item.extra_data["coin"] = stat.get("coin", 0)

                    tags = data.get("data", {}).get("tabs", [])
                    if tags:
                        item.tags = [t.get("name", "") for t in tags if t.get("name")]

                enriched += 1
                if enriched % 10 == 0:
                    logger.info(f"Stats enriched: {enriched}/{len(items)}")
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"Failed stats for aid={item.content_id}: {e}")

        logger.info(f"Stats enriched: {enriched}/{len(items)} videos")

    @staticmethod
    def _parse_duration(length: str) -> float:
        parts = length.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
