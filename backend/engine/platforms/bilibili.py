"""Bilibili platform adapter — uses bilibili-api library for all API calls."""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from bilibili_api import user, video, sync, Credential
from bilibili_api.user import VideoOrder

from backend.models import Platform, ContentType
from .base import (
    BasePlatformAdapter, ContentItem, CreatorInfo, ProgressCallback,
)

logger = logging.getLogger("fapai.adapter.bilibili")


def _make_credential(cookie_str: str | None) -> Credential | None:
    """从 Cookie 字符串创建 bilibili-api 的 Credential 对象"""
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


class BilibiliAdapter(BasePlatformAdapter):
    platform = Platform.BILIBILI

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
        mid = int(platform_uid.strip())
        cred = _make_credential(cookie_str)
        logger.info(f"Bilibili crawl: mid={mid}, max={max_items}, "
                     f"date_from={date_from}, date_to={date_to}, has_credential={cred is not None}")

        # ---- 1. 用户信息 ----
        creator_info = await self._fetch_user_info(mid, cred)

        # ---- 2. 视频列表 ----
        items: list[ContentItem] = []
        page = 1
        page_size = min(50, max_items) if max_items > 0 else 50

        while len(items) < (max_items or 99999):
            try:
                u = user.User(mid, credential=cred)
                data = sync(u.get_videos(ps=page_size, pn=page, order=VideoOrder.PUBDATE))
                vlist = data.get("list", {}).get("vlist", [])
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

                if progress_callback:
                    await progress_callback(len(items), max_items, "running")

                if len(vlist) < page_size:
                    break
                page += 1
                await asyncio.sleep(0.3)

            except Exception as e:
                logger.error(f"Video list page {page} failed: {e}")
                break

        if max_items > 0 and len(items) > max_items:
            items = items[:max_items]

        # ---- 2.5 日期过滤 ----
        items = self._filter_by_date(items, date_from, date_to)

        # ---- 3. 逐视频获取 stat ----
        if items:
            logger.info(f"Fetching stats for {len(items)} videos...")
            await self._enrich_stats(items, cred)

        logger.info(f"Bilibili done: {creator_info.nickname}, {len(items)} videos")
        return creator_info, items

    # ---- 用户信息 ----

    async def _fetch_user_info(self, mid: int, cred: Credential | None) -> CreatorInfo:
        try:
            u = user.User(mid, credential=cred)
            info = sync(u.get_user_info())
            followers = 0
            try:
                # get_user_info might not return follower count directly
                # Use relation stat as fallback
                from bilibili_api import api
                # Just get what's available
                followers = info.get("follower", 0) or info.get("fans", 0)
            except Exception:
                pass
            return CreatorInfo(
                nickname=info.get("name", ""),
                signature=info.get("sign", ""),
                avatar_url=info.get("face", ""),
                follower_count=followers,
                extra_info={"mid": str(mid), "level": info.get("level", 0)},
            )
        except Exception as e:
            logger.warning(f"User info failed: {e}")
        return CreatorInfo()

    # ---- 视频 stat ----

    async def _enrich_stats(self, items: list[ContentItem], cred: Credential | None):
        """逐个调用 video.Video.get_info() 获取准确 stat"""
        enriched = 0
        for item in items:
            bvid = item.extra_data.get("bvid", "")
            aid = item.content_id
            if not bvid and not aid:
                continue

            try:
                v = video.Video(bvid=bvid, credential=cred) if bvid else video.Video(aid=int(aid), credential=cred)
                info = sync(v.get_info())
                stat = info.get("stat", {})
                if not stat:
                    continue

                item.likes = stat.get("like", 0)
                item.shares = stat.get("share", 0)
                item.collects = stat.get("favorite", 0)
                item.plays = stat.get("view", item.plays)
                item.comments = stat.get("reply", item.comments)
                item.extra_data["danmaku"] = stat.get("danmaku", item.extra_data.get("danmaku", 0))
                item.extra_data["coin"] = stat.get("coin", 0)

                # 标签
                tags = info.get("tabs", [])
                if tags:
                    item.tags = [t.get("name", "") for t in tags if t.get("name")]

                enriched += 1
                if enriched % 10 == 0:
                    logger.info(f"Stats: {enriched}/{len(items)}")
                    await asyncio.sleep(0.2)

            except Exception as e:
                logger.warning(f"Stat for {bvid or aid}: {e}")

        logger.info(f"Stats enriched: {enriched}/{len(items)}")

    @staticmethod
    def _filter_by_date(items: list[ContentItem], date_from: str | None, date_to: str | None) -> list[ContentItem]:
        """按日期过滤视频"""
        if not date_from and not date_to:
            return items
        result = []
        for item in items:
            if item.create_time is None:
                result.append(item)  # 无日期的不过滤
                continue
            if date_from and item.create_time.strftime("%Y-%m-%d") < date_from:
                continue
            if date_to and item.create_time.strftime("%Y-%m-%d") > date_to:
                continue
            result.append(item)
        return result

    @staticmethod
    def _parse_duration(length: str) -> float:
        parts = length.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
