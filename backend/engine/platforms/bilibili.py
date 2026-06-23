"""Bilibili platform adapter — HTTP API with WBI signing + view API stats enrichment."""
import asyncio
import hashlib
import logging
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}

MIXIN_KEY = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
]

def _parse_cookies(cookie_str: str) -> dict:
    """Cookie 字符串 → dict"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, _, v = item.partition("=")
            result[k.strip()] = v.strip()
    return result


def _fetch_wbi_keys(cookies: dict | None = None) -> tuple[str, str]:
    """获取 WBI 签名密钥"""
    try:
        resp = Fetcher.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=HEADERS,
            cookies=cookies if cookies else None,
        )
        wbi = resp.json().get("data", {}).get("wbi_img", {})
        img_key = wbi.get("img_url", "").split("/")[-1].split(".")[0]
        sub_key = wbi.get("sub_url", "").split("/")[-1].split(".")[0]
        return img_key, sub_key
    except Exception as e:
        logger.warning(f"Failed to fetch WBI keys: {e}")
        return "", ""


def _wbi_sign(params: dict, cookies: dict | None = None) -> dict:
    """为请求参数添加 WBI 签名"""
    img_key, sub_key = _fetch_wbi_keys(cookies)
    if not img_key or not sub_key:
        return params

    mixin = img_key + sub_key
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
        """通过 B站 HTTP API 抓取用户空间视频。platform_uid = 用户 mid (数字)"""
        mid = platform_uid.strip()

        # 解析 cookie
        cookies = _parse_cookies(cookie_str) if cookie_str else {}
        cookie_keys = [k for k in cookies if k in ("SESSDATA", "bili_jct", "DedeUserID")]
        logger.info(f"Bilibili crawl: mid={mid}, max={max_items}, "
                     f"total_cookies={len(cookies)}, auth_keys={cookie_keys}")

        # 1. 用户信息
        creator_info = await self._fetch_user_info(mid, cookies)

        # 2. 分页获取视频列表
        items: list[ContentItem] = []
        page = 1
        page_size = min(50, max_items) if max_items > 0 else 50

        while len(items) < (max_items or 99999):
            page_items, has_more = await self._fetch_video_page(mid, page, page_size, cookies)
            items.extend(page_items)

            if progress_callback:
                await progress_callback(len(items), max_items, "running")

            if not has_more or not page_items:
                break
            page += 1
            await asyncio.sleep(0.5)

        if max_items > 0 and len(items) > max_items:
            items = items[:max_items]

        # 3. 批量获取互动数据
        if items:
            logger.info(f"Enriching stats for {len(items)} videos...")
            await self._enrich_with_stats(items, cookies)

        logger.info(f"Bilibili done: {creator_info.nickname}, {len(items)} videos")
        return creator_info, items

    async def _fetch_user_info(self, mid: str, cookies: dict) -> CreatorInfo:
        """获取用户信息"""
        try:
            resp = Fetcher.get(
                "https://api.bilibili.com/x/space/wbi/acc/info",
                params=_wbi_sign({"mid": mid}, cookies),
                headers=HEADERS,
                cookies=cookies if cookies else None,
            )
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
            logger.warning(f"User info failed: {e}")
        return CreatorInfo()

    async def _fetch_video_page(self, mid: str, page: int, ps: int, cookies: dict) -> tuple[list[ContentItem], bool]:
        """获取一页视频列表"""
        try:
            params = _wbi_sign({"mid": mid, "ps": ps, "pn": page, "order": "pubdate"}, cookies)
            resp = Fetcher.get(
                "https://api.bilibili.com/x/space/wbi/arc/search",
                params=params,
                headers={**HEADERS, "Referer": f"https://space.bilibili.com/{mid}"},
                cookies=cookies if cookies else None,
            )
            if resp.status != 200 or resp.json().get("code") != 0:
                code = resp.json().get("code", "?") if resp.status == 200 else resp.status
                logger.warning(f"Video list API: code={code}")
                return [], False

            vlist = resp.json().get("data", {}).get("list", {}).get("vlist", [])
            items = []
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
                    likes=0,                              # ↓ 批量 enrich
                    comments=v.get("video_review", 0),    # video_review = 评论数
                    shares=0,                              # ↓ 批量 enrich
                    plays=v.get("play", 0),
                    collects=v.get("favorites", 0),        # 列表API可能有
                    extra_data={
                        "bvid": v.get("bvid", ""),
                        "danmaku": v.get("comment", 0),   # comment = 弹幕
                        "description": v.get("description", ""),
                    },
                ))
            return items, len(vlist) >= ps
        except Exception as e:
            logger.warning(f"Video page {page} failed: {e}")
            return [], False

    async def _enrich_with_stats(self, items: list[ContentItem], cookies: dict):
        """用 view API 批量获取准确的互动数据"""
        enriched = 0
        for item in items:
            try:
                resp = Fetcher.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"aid": item.content_id},
                    headers=HEADERS,
                    cookies=cookies if cookies else None,
                )
                if resp.status != 200:
                    continue
                stat = resp.json().get("data", {}).get("stat", {})
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
                tags = resp.json().get("data", {}).get("tabs", [])
                if tags:
                    item.tags = [t.get("name", "") for t in tags if t.get("name")]

                enriched += 1
                if enriched % 20 == 0:
                    logger.info(f"Stats: {enriched}/{len(items)}")
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.warning(f"Stat for aid={item.content_id}: {e}")

        logger.info(f"Stats enriched: {enriched}/{len(items)}")

    async def _fetch_followers(self, mid: str) -> int:
        try:
            resp = Fetcher.get(
                "https://api.bilibili.com/x/relation/stat",
                params={"vmid": mid},
                headers=HEADERS,
            )
            if resp.status == 200 and resp.json().get("code") == 0:
                return resp.json().get("data", {}).get("follower", 0)
        except Exception:
            pass
        return 0

    @staticmethod
    def _parse_duration(length: str) -> float:
        parts = length.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
