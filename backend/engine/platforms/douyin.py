"""Douyin platform adapter — wraps Playwright-based crawler."""
import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

from backend.models import Platform, ContentType
from .base import (
    BasePlatformAdapter, ContentItem, CreatorInfo, ProgressCallback,
)

logger = logging.getLogger("fapai.adapter.douyin")

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]

class DouyinAdapter(BasePlatformAdapter):
    platform = Platform.DOUYIN

    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int = 50,
        headless: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
        cookie_str: Optional[str] = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[CreatorInfo, list[ContentItem]]:
        from playwright.async_api import async_playwright

        user_url = f"https://www.douyin.com/user/{platform_uid}"
        xhr_data: list[dict] = []
        creator_info = CreatorInfo()

        logger.info(f"Douyin crawl starting: uid={platform_uid}, max_items={max_items}, headless={headless}")

        chrome_exe = next((p for p in CHROME_PATHS if os.path.exists(p)), None)
        if chrome_exe:
            logger.info(f"Using Chrome: {chrome_exe}")
        else:
            logger.warning("No Chrome found in standard paths, will try Playwright Chromium")

        async with async_playwright() as p:
            launch_args = {
                "headless": headless,
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            }
            if chrome_exe:
                launch_args["executable_path"] = chrome_exe

            logger.info("Launching browser...")
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                locale="zh-CN",
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            async def on_response(response):
                if "/aweme/v1/web/aweme/post" in response.url:
                    try:
                        data = await response.json()
                        xhr_data.append(data)
                        total = sum(len(d.get("aweme_list", [])) for d in xhr_data)
                        logger.info(f"Captured XHR: {len(data.get('aweme_list', []))} videos (total={total})")
                        if progress_callback:
                            await progress_callback(total, max_items, "running")
                    except Exception:
                        pass

            page.on("response", on_response)

            logger.info(f"Navigating to: {user_url}")
            try:
                await page.goto(user_url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                logger.warning(f"Page navigation incomplete: {e}")
            await asyncio.sleep(5)

            # Check login wall
            if "login" in page.url or "passport" in page.url:
                logger.warning(f"Login wall detected at: {page.url}")
                if headless:
                    raise RuntimeError("Login required. Set headless=False to scan QR code.")
                print("[Douyin] Please log in via QR code in the browser window, then press Enter...")
                await asyncio.get_event_loop().run_in_executor(None, input)
                logger.info("Login complete, re-navigating...")
                await page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

            # Scroll for more
            logger.info("Starting scroll to load more videos...")
            prev_total = 0
            no_new = 0
            for i in range(20):
                current_total = sum(len(d.get("aweme_list", [])) for d in xhr_data)
                if max_items > 0 and current_total >= max_items:
                    logger.info(f"Reached max_items={max_items}, stopping scroll")
                    break
                if current_total == prev_total:
                    no_new += 1
                    if no_new >= 5:
                        logger.info(f"No new data after {no_new} scrolls, stopping")
                        break
                else:
                    no_new = 0
                prev_total = current_total
                if i % 3 == 0:
                    logger.info(f"Scroll {i+1}/20: {current_total} videos captured so far")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

            logger.info("Closing browser...")
            await browser.close()

        # Extract creator info from first aweme
        items = self._parse_xhr_data(xhr_data, max_items)
        logger.info(f"Parsed {len(items)} content items from {len(xhr_data)} XHR responses")
        for d in xhr_data:
            for aweme in d.get("aweme_list", []):
                author = aweme.get("author", {})
                if author.get("nickname"):
                    creator_info = CreatorInfo(
                        nickname=author.get("nickname", ""),
                        signature=author.get("signature", ""),
                        avatar_url=(author.get("avatar_thumb", {}) or {}).get("url_list", [""])[0],
                        follower_count=author.get("follower_count", 0),
                        extra_info={"uid": author.get("uid", ""), "sec_uid": author.get("sec_uid", "")},
                    )
                break
            if creator_info.nickname:
                break

        # 日期过滤
        if date_from or date_to:
            items = [i for i in items if (
                i.create_time is None
                or (not date_from or i.create_time.strftime("%Y-%m-%d") >= date_from)
                and (not date_to or i.create_time.strftime("%Y-%m-%d") <= date_to)
            )]

        return creator_info, items

    def _parse_xhr_data(self, xhr_data: list[dict], max_items: int) -> list[ContentItem]:
        items: list[ContentItem] = []
        seen_ids = set()
        for data in xhr_data:
            for aweme in data.get("aweme_list", []):
                aid = aweme.get("aweme_id", "")
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)

                stats = aweme.get("statistics", {})
                video = aweme.get("video", {})
                music = aweme.get("music", {})

                tags = [
                    t.get("hashtag_name", "")
                    for t in aweme.get("text_extra", [])
                    if t.get("hashtag_name")
                ]

                ct = aweme.get("create_time", 0)
                create_dt = datetime.fromtimestamp(ct) if ct else None

                cover = ""
                if video.get("cover"):
                    cover = video["cover"].get("url_list", [""])[0]

                images = []
                for img in (aweme.get("image_infos") or {}).values():
                    urls = img.get("url_list", [])
                    if urls:
                        images.append(urls[0])

                items.append(ContentItem(
                    content_id=aid,
                    content_type=ContentType.IMAGE if images else ContentType.VIDEO,
                    title=aweme.get("desc", ""),
                    cover_url=cover,
                    create_time=create_dt,
                    duration_sec=round(video.get("duration", 0) / 1000, 1),
                    url=f"https://www.douyin.com/video/{aid}" if aid else "",
                    tags=tags,
                    music_title=music.get("title", ""),
                    likes=stats.get("digg_count", 0),
                    comments=stats.get("comment_count", 0),
                    shares=stats.get("share_count", 0),
                    plays=stats.get("play_count", 0),
                    collects=stats.get("collect_count", 0),
                    extra_data={"images": images},
                ))

                if max_items > 0 and len(items) >= max_items:
                    return items
        return items
