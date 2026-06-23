"""
Douyin Demo Spider (Spider 框架版) — 使用 Scrapling Spider 类

这是更符合 Scrapling 设计理念的写法：
- 继承 Spider 基类
- 通过 configure_sessions() 配置浏览器会话
- 使用 parse() 生成器处理响应
- 自动获得暂停/恢复、去重、并发控制等能力

用法:
    python douyin_spider_framework.py

依赖:
    pip install "scrapling[fetchers]"
    playwright install chromium
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from scrapling.spiders import Spider, Request
from scrapling.engines.toolbelt.custom import Response


# ============================================================
# 配置
# ============================================================

TARGET_SEC_UID = "MS4wLjABAAAAuWZZlvIC2SHOWYQ4LkdxH1zcYBZWtVtARoh9wCpYsPYA3TeZ2b8g7p4hBQOEIj3y"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "douyin_videos_spider.json"


# ============================================================
# Spider 定义
# ============================================================

class DouyinUserSpider(Spider):
    """
    抖音用户主页爬虫

    使用 DynamicSession 来渲染 JS 页面并拦截 API 调用。
    """

    name = "douyin_user"

    # 不设置 start_urls，而是通过 start_requests() 动态构建
    start_urls: list[str] = []

    # 并发控制 — 抖音需要礼貌的请求频率
    concurrent_requests = 1
    download_delay = 2.0  # 请求间隔(秒)

    # 日志
    logging_level = 20  # INFO

    # 存储的字段
    sec_uid: str = ""
    max_videos: int = 30

    def __init__(
        self,
        sec_uid: str = TARGET_SEC_UID,
        max_videos: int = 30,
        **kwargs,
    ):
        self.sec_uid = sec_uid
        self.max_videos = max_videos
        self._collected: List[Dict[str, Any]] = []
        super().__init__(**kwargs)

    # ---- Session 配置 ----

    def configure_sessions(self, manager):
        """
        配置 DynamicSession (Playwright/Chromium) 来处理 JS 渲染页面。

        DynamicSession 会启动一个真实的 Chromium 浏览器来加载页面，
        执行 JavaScript，然后返回渲染后的 HTML。
        """
        from scrapling.fetchers import AsyncDynamicSession

        manager.add(
            "default",
            AsyncDynamicSession(
                headless=True,          # 无头模式
                network_idle=True,      # 等待网络空闲
                timeout=30000,          # 30秒超时
                block_ads=True,         # 屏蔽广告
                # 拦截用户作品列表的 API 调用
                capture_xhr="aweme/v1/web/aweme/post",
                extra_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                useragent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            ),
        )

    # ---- 起始请求 ----

    async def start_requests(self) -> AsyncGenerator[Request, None]:
        """生成对抖音用户主页的初始请求"""
        url = f"https://www.douyin.com/user/{self.sec_uid}"
        self.logger.info(f"开始抓取: {url}")
        yield Request(
            url=url,
            sid="default",
            meta={"page_num": 1},
        )

    # ---- 解析逻辑 ----

    async def parse(
        self, response: Response
    ) -> AsyncGenerator[Dict[str, Any] | Request | None, None]:
        """
        解析抖音用户主页响应。

        由于配置了 capture_xhr，拦截到的 API 响应会存储在
        response.captured_xhr 中，可以直接读取 JSON 数据。
        """
        page_num = response.meta.get("page_num", 1)

        # --------------------------------------------------
        # 方法1: 从捕获的 XHR 响应中提取数据 (推荐)
        # --------------------------------------------------
        captured: List = getattr(response, "captured_xhr", [])

        if captured:
            self.logger.info(
                f"[第{page_num}页] 捕获到 {len(captured)} 条 XHR 响应"
            )

            for xhr_resp in captured:
                try:
                    data = xhr_resp.json()
                except Exception:
                    continue

                # 提取 aweme 数据
                aweme_list = data.get("aweme_list", [])
                for aweme in aweme_list:
                    item = self._extract_aweme(aweme)
                    if item:
                        self._collected.append(item)
                        yield item

                # 检查是否有更多页
                has_more = data.get("has_more", 0)
                max_cursor = data.get("max_cursor", 0)

                if (
                    has_more
                    and max_cursor
                    and len(self._collected) < self.max_videos
                ):
                    # 可以通过 API 参数直接请求下一页
                    # 这里简单跳过，避免过深爬取
                    self.logger.info(
                        f"还有更多视频 (has_more={has_more}, "
                        f"cursor={max_cursor})"
                    )

        else:
            # --------------------------------------------------
            # 方法2: 从 HTML 中提取 (备用)
            # --------------------------------------------------
            self.logger.info(
                f"[第{page_num}页] 无 XHR 捕获，尝试解析 HTML..."
            )
            # 抖音页面结构可能随时变化，这里做基本尝试
            items = response.css('[data-e2e="user-post-list"] > div')
            for item in items[: self.max_videos]:
                cover_img = item.css("img")
                desc = cover_img.css("::attr(alt)").get() or ""
                cover = cover_img.css("::attr(src)").get() or ""
                if desc:
                    item_data = {
                        "aweme_id": "",
                        "desc": desc,
                        "cover_url": cover,
                        "source": "html",
                        "statistics": {
                            "likes": 0,
                            "comments": 0,
                            "shares": 0,
                            "plays": 0,
                        },
                    }
                    self._collected.append(item_data)
                    yield item_data

    # ---- 数据提取 ----

    def _extract_aweme(self, aweme: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 aweme JSON 中提取结构化数据"""
        try:
            aweme_id = aweme.get("aweme_id", "")

            # 跳过已收集的
            if aweme_id and any(
                v.get("aweme_id") == aweme_id for v in self._collected
            ):
                return None

            stats = aweme.get("statistics", {})
            video = aweme.get("video", {})
            author = aweme.get("author", {})
            music = aweme.get("music", {})

            # 话题标签
            hashtags = [
                t.get("hashtag_name", "")
                for t in aweme.get("text_extra", [])
                if t.get("hashtag_name")
            ]

            return {
                "aweme_id": aweme_id,
                "desc": aweme.get("desc", ""),
                "create_time": self._format_time(
                    aweme.get("create_time", 0)
                ),
                "duration_sec": round(video.get("duration", 0) / 1000, 1),
                "author": {
                    "nickname": author.get("nickname", ""),
                    "uid": author.get("uid", ""),
                    "sec_uid": author.get("sec_uid", ""),
                },
                "statistics": {
                    "likes": stats.get("digg_count", 0),
                    "comments": stats.get("comment_count", 0),
                    "shares": stats.get("share_count", 0),
                    "plays": stats.get("play_count", 0),
                },
                "cover_url": (
                    video.get("cover", {}).get("url_list", [""])[0]
                ),
                "hashtags": hashtags,
                "music": music.get("title", ""),
                "share_url": (
                    f"https://www.douyin.com/video/{aweme_id}"
                    if aweme_id
                    else ""
                ),
            }
        except Exception:
            return None

    @staticmethod
    def _format_time(ts: int) -> str:
        if not ts:
            return ""
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    # ---- 钩子 ----

    async def on_start(self, resuming: bool = False):
        if resuming:
            self.logger.info("从检查点恢复爬取...")
        else:
            self.logger.info("开始爬取抖音用户主页...")

    async def on_scraped_item(self, item: Dict[str, Any]):
        """每抓取到一条数据时调用"""
        stats = item.get("statistics", {})
        self.logger.info(
            f"  ✅ {item['desc'][:40]} | "
            f"👍 {stats.get('likes', 0):,} "
            f"💬 {stats.get('comments', 0):,} "
            f"🔄 {stats.get('shares', 0):,}"
        )
        return item

    async def on_close(self):
        """爬取结束时保存结果"""
        self.logger.info(f"爬取结束，共收集 {len(self._collected)} 个视频")

        result = {
            "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sec_uid": self.sec_uid,
            "total_videos": len(self._collected),
            "videos": self._collected,
        }

        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        self.logger.info(f"结果已保存到: {OUTPUT_FILE}")


# ============================================================
# 入口
# ============================================================

def main():
    """运行爬虫"""
    spider = DouyinUserSpider(
        sec_uid=TARGET_SEC_UID,
        max_videos=30,
        # crawldir="./douyin_checkpoints",  # 开启暂停/恢复功能
    )

    # spider.start() 是官方入口，内部通过 anyio 处理异步
    result = spider.start()

    stats = result.stats
    print(f"\n爬取统计:")
    print(f"  请求数: {stats.requests_count}")
    print(f"  成功: {stats.response_status_count.get('status_200', 0)}")
    print(f"  失败: {stats.failed_requests_count}")
    print(f"  产出: {len(result.items)} 条数据 / {stats.items_scraped} 条入库")
    print(f"  耗时: {stats.elapsed_seconds:.1f}s")
    print(f"  暂停: {result.paused}")


if __name__ == "__main__":
    main()
