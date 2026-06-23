"""Bilibili platform adapter — uses HTTP API with WBI signing."""
import hashlib
import logging
import time
import urllib.parse
from datetime import datetime
from functools import lru_cache
from typing import Optional

from scrapling.fetchers import Fetcher

from backend.models import Platform, ContentType
from .base import (
    BasePlatformAdapter, ContentItem, CreatorInfo, ProgressCallback,
)

logger = logging.getLogger("fapai.adapter.bilibili")

BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

# WBI 签名密钥缓存（60秒刷新）
_wbi_keys: dict = {"img_key": "", "sub_key": "", "expires_at": 0}
_mixin_key_table = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
]


# 全局 cookie jar，维持 B站 API 会话
_session_cookies: dict = {}

def _fetch_wbi_keys() -> dict:
    """获取并缓存 WBI 签名密钥，同时维持 cookie 会话"""
    global _wbi_keys, _session_cookies
    now = time.time()
    if _wbi_keys["expires_at"] > now:
        return _wbi_keys

    try:
        resp = Fetcher.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=BILIBILI_HEADERS,
            stealthy_headers=True,
        )
        # 保存 cookies 供后续请求使用
        if resp.cookies:
            _session_cookies = {c["name"]: c["value"] for c in resp.cookies if isinstance(c, dict)}

        data = resp.json().get("data", {}).get("wbi_img", {})
        img_url = data.get("img_url", "")
        sub_url = data.get("sub_url", "")

        # 从 URL 中提取 key：/bfs/wbi/xxx.png → xxx
        img_key = img_url.split("/")[-1].split(".")[0] if img_url else ""
        sub_key = sub_url.split("/")[-1].split(".")[0] if sub_url else ""

        _wbi_keys = {"img_key": img_key, "sub_key": sub_key, "expires_at": now + 300}
        logger.info(f"WBI keys refreshed (img={img_key[:8]}..., sub={sub_key[:8]}...)")
    except Exception as e:
        logger.warning(f"Failed to fetch WBI keys: {e}")

    return _wbi_keys


def _sign_params(params: dict) -> dict:
    """为请求参数添加 WBI 签名 (w_rid + wts)"""
    keys = _fetch_wbi_keys()
    mixin = keys["img_key"] + keys["sub_key"]
    if not mixin:
        return params  # 无密钥时跳过签名

    # 拼接混合密钥
    mixed = "".join(mixin[i] for i in _mixin_key_table if i < len(mixin))[:32]

    # 排序参数
    params["wts"] = int(time.time())
    sorted_params = sorted(params.items())
    query = urllib.parse.urlencode(sorted_params)

    # MD5(query + mixed_key)
    w_rid = hashlib.md5((query + mixed).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


class BilibiliAdapter(BasePlatformAdapter):
    platform = Platform.BILIBILI

    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int = 50,
        headless: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[CreatorInfo, list[ContentItem]]:
        """通过 B站 HTTP API 抓取用户空间视频。platform_uid = 用户 mid (数字)"""
        mid = platform_uid.strip()
        logger.info(f"Bilibili crawl starting: mid={mid}, max_items={max_items}")

        # 1. 获取用户基本信息
        creator_info = await self._fetch_user_info(mid)

        # 2. 分页获取视频列表
        items: list[ContentItem] = []
        page = 1
        page_size = min(50, max_items) if max_items > 0 else 50

        while True:
            if max_items > 0 and len(items) >= max_items:
                break

            logger.info(f"Fetching page {page} (have {len(items)} items so far)...")
            page_items, has_more = await self._fetch_video_page(mid, page, page_size)
            items.extend(page_items)

            if progress_callback:
                await progress_callback(len(items), max_items, "running")

            if not has_more or not page_items:
                break
            page += 1

        # 截断
        if max_items > 0 and len(items) > max_items:
            items = items[:max_items]

        logger.info(
            f"Bilibili crawl done: mid={mid}, nickname='{creator_info.nickname}', "
            f"total_items={len(items)}"
        )
        return creator_info, items

    async def _fetch_user_info(self, mid: str) -> CreatorInfo:
        """获取用户基本信息"""
        url = "https://api.bilibili.com/x/space/wbi/acc/info"
        try:
            params = _sign_params({"mid": mid})
            resp = Fetcher.get(url, params=params, headers=BILIBILI_HEADERS,
                              cookies=_session_cookies if _session_cookies else None)
            if resp.status != 200:
                logger.warning(f"User info API returned {resp.status}")
                return CreatorInfo()

            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"User info API error: code={data.get('code')}, msg={data.get('message')}")
                return CreatorInfo()

            user = data.get("data", {})
            # 获取粉丝数 (单独接口)
            follower_count = await self._fetch_follower_count(mid)

            return CreatorInfo(
                nickname=user.get("name", ""),
                signature=user.get("sign", ""),
                avatar_url=user.get("face", ""),
                follower_count=follower_count,
                extra_info={
                    "mid": mid,
                    "level": user.get("level", 0),
                    "sex": user.get("sex", ""),
                    "official": user.get("official", {}),
                },
            )
        except Exception as e:
            logger.error(f"Failed to fetch user info: {e}")
            return CreatorInfo()

    async def _fetch_video_page(
        self, mid: str, page: int, page_size: int
    ) -> tuple[list[ContentItem], bool]:
        """获取一页视频列表。返回 (items, has_more)"""
        url = "https://api.bilibili.com/x/space/wbi/arc/search"
        try:
            params = _sign_params({
                "mid": mid,
                "ps": page_size,
                "pn": page,
                "order": "pubdate",
            })
            resp = Fetcher.get(url, params=params, headers=BILIBILI_HEADERS,
                              cookies=_session_cookies if _session_cookies else None)
            if resp.status != 200:
                logger.warning(f"Video list API returned {resp.status}")
                return [], False

            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"Video list API error: code={data.get('code')}, msg={data.get('message')}")
                return [], False

            page_data = data.get("data", {})
            vlist = page_data.get("list", {}).get("vlist", [])

            items = []
            for v in vlist:
                # 发布时间
                created = v.get("created", 0)
                create_dt = datetime.fromtimestamp(created) if created else None

                # 时长 — B站返回的是 "mm:ss" 格式字符串
                length_str = v.get("length", "0:00")
                duration = self._parse_duration(length_str)

                # aid 作为 content_id，bvid 存 extra
                aid = str(v.get("aid", ""))

                items.append(ContentItem(
                    content_id=aid,
                    content_type=ContentType.VIDEO,
                    title=v.get("title", ""),
                    cover_url=v.get("pic", ""),
                    create_time=create_dt,
                    duration_sec=duration,
                    url=f"https://www.bilibili.com/video/av{aid}" if aid else "",
                    tags=[],  # 列表接口不含标签，需单独请求
                    music_title="",
                    likes=0,       # 列表接口不含互动数据
                    comments=v.get("comment", 0),
                    shares=0,
                    plays=v.get("play", 0),
                    collects=0,
                    extra_data={
                        "bvid": v.get("bvid", ""),
                        "aid": aid,
                        "video_review": v.get("video_review", 0),
                        "description": v.get("description", ""),
                    },
                ))

            has_more = len(vlist) >= page_size
            return items, has_more

        except Exception as e:
            logger.error(f"Failed to fetch video page {page}: {e}")
            return [], False

    async def _fetch_follower_count(self, mid: str) -> int:
        """获取粉丝数"""
        try:
            params = _sign_params({"vmid": mid})
            resp = Fetcher.get(
                "https://api.bilibili.com/x/relation/stat",
                params=params,
                headers=BILIBILI_HEADERS,
            )
            if resp.status == 200 and resp.json().get("code") == 0:
                return resp.json().get("data", {}).get("follower", 0)
        except Exception as e:
            logger.warning(f"Failed to fetch follower count: {e}")
        return 0

    @staticmethod
    def _parse_duration(length: str) -> float:
        """将 "mm:ss" 或 "hh:mm:ss" 转为秒"""
        parts = length.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
