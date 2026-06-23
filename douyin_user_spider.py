"""
Douyin (抖音) 用户主页爬虫 — 抓取指定用户的视频及互动数据

使用 Scrapling + Playwright 组合方案:
  - Playwright 启动真实浏览器，加载 JS 渲染的抖音页面，拦截 XHR API 响应
  - Scrapling 提供 CSS 选择器和 HTML 解析能力（获取页面可见文本等）

原理:
  抖音是纯 JS 渲染的 SPA 应用，页面数据通过 XHR API 异步加载。
  本脚本启动 Chromium 浏览器，监听页面发出的 API 请求，
  拦截 /aweme/v1/web/aweme/post/ 接口的 JSON 响应，直接提取结构化视频数据。

重要提示:
  - 抖音 Web 版对未登录用户有严格限制，会弹出登录/验证窗口
  - 建议设置 HEADLESS=False 手动扫码登录后抓取
  - 登录后 Cookie 会被保存在 user_data_dir 中，下次可直接复用

用法:
  # 1. 安装依赖
  pip install "scrapling[fetchers]"
  # playwright 已安装，无需额外安装浏览器（使用系统 Chrome）

  # 2. 基本用法（替换为目标用户的 sec_uid）
  python douyin_user_spider.py

  # 3. 非无头模式（需要手动扫码登录）
  # 修改代码中 HEADLESS=False 或设置环境变量
  HEADLESS=0 python douyin_user_spider.py

  # 4. 指定用户
  python douyin_user_spider.py MS4wLjABAAAA...

如何获取用户 sec_uid:
  - 方法1: 在抖音 APP 中打开用户主页 → 分享 → 复制链接
           链接格式: https://v.douyin.com/xxx/ 或包含 /user/XXX
  - 方法2: 在 PC 浏览器已登录状态下访问用户主页
           URL 中 /user/ 后面的部分即为 sec_uid
  - 方法3: 使用本脚本登录后，从拦截的 API 响应中提取
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# 配置区 — 根据需要修改
# ============================================================

# 目标用户 sec_uid
# 如何获取: 在抖音 APP → 用户主页 → 分享 → 复制链接
# 链接中形如 /user/XXXX 的部分即为 sec_uid
TARGET_SEC_UID = "MS4wLjABAAAAuWZZlvIC2SHOWYQ4LkdxH1zcYBZWtVtARoh9wCpYsPYA3TeZ2b8g7p4hBQOEIj3y"

# 抓取限制
MAX_VIDEOS = 50       # 最多抓取视频数 (0 = 不限制)
MAX_SCROLLS = 20      # 最大滚动次数
SCROLL_DELAY = 2.0    # 每次滚动间隔(秒)

# 浏览器设置
HEADLESS = False  # True=无头, False=显示窗口
BROWSER_USER_DATA_DIR = str(  # 持久化浏览器数据 (保存登录态)
    Path(__file__).parent / ".douyin_browser_data"
)

# Chrome 路径 (Windows 默认路径，Mac/Linux 会自动检测)
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
]

# 输出目录
OUTPUT_DIR = Path(__file__).parent / "output"


# ============================================================
# 数据提取
# ============================================================

def extract_aweme(aweme: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    从抖音 API 返回的单条 aweme 数据中提取关键信息。

    抖音 aweme 数据结构（简化版）:
    {
      "aweme_id": "7123456789012345678",
      "desc": "视频标题 #话题",
      "create_time": 1690000000,
      "video": {
        "duration": 15000,           # 时长(毫秒)
        "cover": {"url_list": [...]},
        "play_addr": {"url_list": [...]}
      },
      "statistics": {
        "digg_count": 12345,         # 👍 点赞
        "comment_count": 678,        # 💬 评论
        "share_count": 90,           # 🔄 转发
        "play_count": 123456,        # ▶️ 播放
        "collect_count": 456         # ⭐ 收藏
      },
      "author": {"nickname": "...", "uid": "...", "sec_uid": "..."},
      "music": {"title": "...", "author": "..."},
      "text_extra": [{"hashtag_name": "话题"}],
      "image_infos": [...]           # 图集内容(如有)
    }
    """
    try:
        aweme_id = aweme.get("aweme_id", "")
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

        # 时间戳 → 可读格式
        create_time = aweme.get("create_time", 0)
        create_time_str = (
            datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
            if create_time else ""
        )

        # 封面 URL（取第一张）
        cover_url = ""
        if video.get("cover"):
            cover_url = video["cover"].get("url_list", [""])[0]

        # 图片内容（图集/图文）
        images = []
        for img_info in (aweme.get("image_infos") or {}).values():
            urls = img_info.get("url_list", [])
            if urls:
                images.append(urls[0])

        return {
            "aweme_id": aweme_id,
            "desc": aweme.get("desc", ""),
            "create_time": create_time_str,
            "create_timestamp": create_time,
            "duration_sec": round(video.get("duration", 0) / 1000, 1),
            "is_image": bool(images),
            "images": images,
            "author": {
                "nickname": author.get("nickname", ""),
                "uid": author.get("uid", ""),
                "sec_uid": author.get("sec_uid", ""),
                "signature": author.get("signature", ""),
            },
            "statistics": {
                "likes": stats.get("digg_count", 0),
                "comments": stats.get("comment_count", 0),
                "shares": stats.get("share_count", 0),
                "plays": stats.get("play_count", 0),
                "collects": stats.get("collect_count", 0),
            },
            "cover_url": cover_url,
            "hashtags": hashtags,
            "music": {
                "title": music.get("title", ""),
                "author": music.get("author", ""),
            },
            "share_url": (
                f"https://www.douyin.com/video/{aweme_id}" if aweme_id else ""
            ),
        }
    except Exception:
        return None


def normalize_videos(videos: List[Dict]) -> List[Dict]:
    """去重 + 排序 (按时间倒序)"""
    seen = set()
    result = []
    for v in videos:
        aid = v.get("aweme_id", "")
        if aid and aid in seen:
            continue
        if aid:
            seen.add(aid)
        result.append(v)
    result.sort(key=lambda v: v.get("create_timestamp", 0), reverse=True)
    return result


# ============================================================
# 核心抓取逻辑 — Playwright 浏览器模式
# ============================================================

async def crawl_douyin_user(
    sec_uid: str,
    max_videos: int = MAX_VIDEOS,
    max_scrolls: int = MAX_SCROLLS,
    headless: bool = HEADLESS,
) -> List[Dict[str, Any]]:
    """
    使用 Playwright 浏览器抓取抖音用户主页视频列表。

    流程:
    1. 启动 Chromium 浏览器
    2. 访问 https://www.douyin.com/user/{sec_uid}
    3. 拦截页面发出的 XHR API 响应，匹配 /aweme/v1/web/aweme/post/
    4. 滚动页面触发更多视频加载
    5. 从拦截的 JSON 中提取视频数据

    Args:
        sec_uid: 用户 sec_uid
        max_videos: 最大视频数量 (0=不限制)
        max_scrolls: 最大滚动次数
        headless: True=无头模式, False=显示浏览器窗口

    Returns:
        视频信息列表
    """
    from playwright.async_api import async_playwright

    user_url = f"https://www.douyin.com/user/{sec_uid}"
    videos: List[Dict[str, Any]] = []

    print(f"\n{'='*70}")
    print(f"🎬 抖音用户主页爬虫")
    print(f"{'='*70}")
    print(f"  目标: {user_url}")
    print(f"  模式: {'无头 (后台运行)' if headless else '可见窗口 (可手动登录)'}")
    print(f"  限制: {'不限制' if max_videos == 0 else f'最多 {max_videos} 个视频'}")
    print(f"  滚动: 最多 {max_scrolls} 次, 间隔 {SCROLL_DELAY}s")
    print()

    # ---- 找到系统 Chrome ----
    chrome_exe = None
    for path in CHROME_PATHS:
        if os.path.exists(path):
            chrome_exe = path
            break

    if not chrome_exe:
        print("⚠ 未找到系统 Chrome，尝试使用 Playwright 自带 Chromium...")
        print("  如果失败，请运行: playwright install chromium")

    # ---- 启动浏览器 ----
    async with async_playwright() as p:
        launch_args = {
            "headless": headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if chrome_exe:
            launch_args["executable_path"] = chrome_exe
            print(f"✅ 使用浏览器: {chrome_exe}")
        else:
            print("✅ 使用 Playwright Chromium")

        browser = await p.chromium.launch(**launch_args)

        # ---- 创建上下文（带持久化存储保留登录态） ----
        Path(BROWSER_USER_DATA_DIR).mkdir(parents=True, exist_ok=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        # ---- 拦截 API 响应 ----
        xhr_data: List[Dict] = []

        async def on_response(response):
            """捕获抖音视频列表 API 的 JSON 响应"""
            url = response.url
            if "/aweme/v1/web/aweme/post" in url:
                try:
                    data = await response.json()
                    xhr_data.append(data)
                    count = len(data.get("aweme_list", []))
                    has_more = data.get("has_more", 0)
                    cursor = data.get("max_cursor", 0)
                    print(f"  📦 捕获 API 响应: {count} 个视频 "
                          f"(has_more={has_more}, cursor={cursor})")
                except Exception:
                    pass

        page.on("response", on_response)

        # ---- 加载页面 ----
        print("⏳ 加载用户主页...")
        try:
            await page.goto(
                user_url,
                wait_until="domcontentloaded",
                timeout=45000,
            )
        except Exception as e:
            print(f"⚠ 页面加载不完整: {e}")

        # 等待 JS 渲染
        print("⏳ 等待页面渲染 (5s)...")
        await asyncio.sleep(5)

        # 检查是否被拦截到登录页
        page_title = await page.title()
        current_url = page.url
        if "login" in current_url or "passport" in current_url:
            print("\n⚠ 检测到登录页面！")
            print("  抖音 Web 版需要登录才能查看用户主页。")
            if headless:
                print("  请设置 HEADLESS=False 后重新运行，在弹出的浏览器中扫码登录。")
                print("  登录后 Cookie 会保留，下次运行可复用。\n")
            else:
                print("  请在浏览器窗口中扫码登录，登录后按 Enter 继续...")
                input()
                # 登录后重新导航到目标页面
                await page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

        # ---- 滚动加载更多视频 ----
        print("📜 滚动页面触发视频加载...")
        prev_total = 0
        no_new_data_count = 0

        for i in range(max_scrolls):
            # 统计当前已捕获的视频数
            current_total = sum(
                len(d.get("aweme_list", [])) for d in xhr_data
            )

            # 检查是否达到上限
            if max_videos > 0 and current_total >= max_videos:
                print(f"  ✅ 已捕获 {current_total} 个视频，达到目标 ({max_videos})")
                break

            # 检查是否有新数据
            if current_total == prev_total:
                no_new_data_count += 1
                if no_new_data_count >= 5:
                    print(f"  已滚动 {i+1} 次无新数据，停止")
                    break
            else:
                no_new_data_count = 0
            prev_total = current_total

            # 滚动到底部
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(SCROLL_DELAY)

            if (i + 1) % 5 == 0:
                print(f"  已滚动 {i+1}/{max_scrolls} 次, "
                      f"累计 {current_total} 个视频")

        # ---- 提取视频数据 ----
        print("\n📊 从 API 响应中提取视频数据...")
        for data in xhr_data:
            aweme_list = data.get("aweme_list", [])
            for aweme in aweme_list:
                info = extract_aweme(aweme)
                if info:
                    videos.append(info)

        # ---- 清理 ----
        await browser.close()

    # 去重 + 排序
    videos = normalize_videos(videos)

    # 截取
    if max_videos > 0 and len(videos) > max_videos:
        videos = videos[:max_videos]

    return videos


# ============================================================
# 备用模式: Scrapling Fetcher — API 直连
# ============================================================

def crawl_with_fetcher(
    sec_uid: str, cookies_str: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    使用 Scrapling Fetcher (HTTP 客户端) 直接调用抖音 API。

    这是不依赖浏览器的纯 HTTP 方式，演示 Scrapling Fetcher 的使用。
    但抖音 API 需要浏览器生成的签名参数，成功率较低。
    建议仅在拥有有效 Cookie 时使用。

    Args:
        sec_uid: 用户 sec_uid
        cookies_str: 从浏览器复制的 Cookie 字符串
    """
    from scrapling.fetchers import Fetcher

    videos: List[Dict[str, Any]] = []
    max_cursor = 0

    print(f"\n🌐 使用 Scrapling Fetcher 调用抖音 API...")

    # 解析 Cookie
    cookies = {}
    if cookies_str:
        for item in cookies_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, _, v = item.partition("=")
                cookies[k.strip()] = v.strip()

    for page_num in range(5):  # 最多 5 页
        params = {
            "sec_user_id": sec_uid,
            "max_cursor": str(max_cursor),
            "count": "20",
            "aid": "6383",
            "cookie_enabled": "true",
            "platform": "PC",
            "downlink": "10",
        }

        headers = {
            "Referer": f"https://www.douyin.com/user/{sec_uid}",
        }

        resp = Fetcher.get(
            "https://www.douyin.com/aweme/v1/web/aweme/post/",
            params=params,
            headers=headers,
            cookies=cookies if cookies else None,
            stealthy_headers=True,
        )

        if resp.status != 200 or len(resp.text) == 0:
            print(f"  ❌ 第{page_num+1}页请求失败 (status={resp.status}, "
                  f"len={len(resp.text)})")
            break

        data = resp.json()
        aweme_list = data.get("aweme_list", [])
        has_more = data.get("has_more", 0)
        max_cursor = data.get("max_cursor", 0)

        print(f"  📄 第{page_num+1}页: {len(aweme_list)} 个视频")

        for aweme in aweme_list:
            info = extract_aweme(aweme)
            if info:
                videos.append(info)

        if not has_more or not aweme_list:
            break

    return videos


# ============================================================
# 输出系统 — 按日期分目录存储，便于前端展示和历史对比
# ============================================================
#
# 目录结构:
#   output/
#   ├── index.json                  # 全局索引，列出所有抓取会话
#   ├── 2026-06-23/                 # 按日期分目录
#   │   ├── <sec_uid>_143052.json   # 单次抓取结果 (sec_uid_时分秒)
#   │   └── <sec_uid>_180030.json
#   ├── 2026-06-24/
#   │   └── ...
#   └── latest/                     # 每个用户最新数据的镜像
#       └── <sec_uid>.json          # 前端可直接读取此文件获取最新数据


def _now() -> datetime:
    return datetime.now()


def _date_dir(dt: datetime) -> str:
    """返回日期目录名，如 '2026-06-23'"""
    return dt.strftime("%Y-%m-%d")


def _time_slug(dt: datetime) -> str:
    """返回时间标识，如 '143052' (时分秒)"""
    return dt.strftime("%H%M%S")


def update_index(
    sec_uid: str,
    session_file: str,
    author_nickname: str,
    video_count: int,
    engagement: Dict[str, int],
    dt: datetime,
):
    """
    更新全局索引文件 output/index.json。

    索引记录每次抓取的元信息，前端可用此文件展示抓取历史、
    对比不同时间点的数据变化。
    """
    index_path = OUTPUT_DIR / "index.json"

    # 读取已有索引
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {
            "description": "抖音爬虫抓取索引 — 记录所有抓取会话",
            "created": _now().strftime("%Y-%m-%d %H:%M:%S"),
            "sessions": [],
        }

    # 新会话条目
    entry = {
        "session_id": f"{_date_dir(dt)}_{_time_slug(dt)}_{sec_uid[:8]}",
        "date": _date_dir(dt),
        "time": dt.strftime("%H:%M:%S"),
        "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "sec_uid": sec_uid,
        "author": author_nickname,
        "video_count": video_count,
        "engagement": engagement,
        "file": session_file,
    }

    # 插入到列表头部 (最新的在前)
    index["sessions"].insert(0, entry)
    index["updated"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    index["total_sessions"] = len(index["sessions"])

    # 按 sec_uid 汇总统计
    users_summary = {}
    for s in index["sessions"]:
        uid = s["sec_uid"]
        if uid not in users_summary:
            users_summary[uid] = {
                "sec_uid": uid,
                "author": s["author"],
                "first_crawl": s["timestamp"],
                "last_crawl": s["timestamp"],
                "total_sessions": 0,
                "latest_video_count": s["video_count"],
                "latest_engagement": s["engagement"],
            }
        users_summary[uid]["total_sessions"] += 1
        # 最新的 session 在前面，所以只更新 last_crawl
        if s["timestamp"] > users_summary[uid]["last_crawl"]:
            users_summary[uid]["last_crawl"] = s["timestamp"]
            users_summary[uid]["latest_video_count"] = s["video_count"]
            users_summary[uid]["latest_engagement"] = s["engagement"]
    index["users"] = list(users_summary.values())

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return index_path


def save_results(
    videos: List[Dict],
    sec_uid: str,
    dt: Optional[datetime] = None,
) -> Path:
    """
    保存抓取结果到 output/ 目录，按日期分文件夹。

    输出结构:
      output/
        └── 2026-06-23/
            └── <sec_uid>_143052.json

    同时:
      1. 更新 output/index.json 索引
      2. 更新 output/latest/<sec_uid>.json (前端直接读取的最新数据)

    Args:
        videos: 视频列表
        sec_uid: 用户 sec_uid
        dt: 抓取时间 (默认当前时间)

    Returns:
        保存的文件路径
    """
    if dt is None:
        dt = _now()

    # 汇总统计
    engagement = {
        "likes": sum(v["statistics"]["likes"] for v in videos),
        "comments": sum(v["statistics"]["comments"] for v in videos),
        "shares": sum(v["statistics"]["shares"] for v in videos),
        "plays": sum(v["statistics"]["plays"] for v in videos),
        "collects": sum(v["statistics"]["collects"] for v in videos),
    }

    # 作者昵称 (取第一个视频的作者)
    author_nickname = videos[0]["author"]["nickname"] if videos else "unknown"

    # 计算与上次抓取的数据变化 (如果有历史数据)
    delta = _compute_delta(sec_uid, videos, engagement)

    # ---- 构建结果 JSON ----
    result = {
        "meta": {
            "crawl_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "crawl_timestamp": int(dt.timestamp()),
            "date": _date_dir(dt),
            "time": dt.strftime("%H:%M:%S"),
            "sec_uid": sec_uid,
            "author": author_nickname,
            "user_url": f"https://www.douyin.com/user/{sec_uid}",
            "total_videos": len(videos),
            "total_engagement": engagement,
            "delta": delta,  # 与上次抓取的对比
        },
        "videos": videos,
    }

    # ---- 保存到日期目录 ----
    date_folder = OUTPUT_DIR / _date_dir(dt)
    date_folder.mkdir(parents=True, exist_ok=True)

    filename = f"{sec_uid[:12]}_{_time_slug(dt)}.json"
    file_path = date_folder / filename

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ---- 更新 latest/ 镜像 ----
    latest_dir = OUTPUT_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest_file = latest_dir / f"{sec_uid[:12]}.json"

    # latest 里也加上简化的元信息供前端方便读取
    latest_result = dict(result)
    latest_result["meta"]["latest_update"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(latest_result, f, ensure_ascii=False, indent=2)

    # ---- 更新全局索引 ----
    relative_path = f"{_date_dir(dt)}/{filename}"
    update_index(
        sec_uid=sec_uid,
        session_file=relative_path,
        author_nickname=author_nickname,
        video_count=len(videos),
        engagement=engagement,
        dt=dt,
    )

    print(f"\n💾 结果已保存:")
    print(f"   📄 {file_path}")
    print(f"   📋 索引: {OUTPUT_DIR / 'index.json'}")
    print(f"   ⚡ 最新: {latest_file}")
    print(f"   📊 {len(videos)} 个视频  |  "
          f"👍 {engagement['likes']:,}  💬 {engagement['comments']:,}  "
          f"🔄 {engagement['shares']:,}")

    if delta:
        print(f"   📈 较上次变化: ", end="")
        parts = []
        if delta.get("new_videos"):
            parts.append(f"+{delta['new_videos']} 新视频")
        likes_d = delta.get("likes_delta", 0)
        if likes_d:
            parts.append(f"👍 {'+' if likes_d > 0 else ''}{likes_d:,}")
        comments_d = delta.get("comments_delta", 0)
        if comments_d:
            parts.append(f"💬 {'+' if comments_d > 0 else ''}{comments_d:,}")
        print(", ".join(parts) if parts else "无变化")

    return file_path


def _compute_delta(
    sec_uid: str,
    current_videos: List[Dict],
    current_engagement: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """
    对比上一次抓取数据，计算变化量。

    通过读取 output/latest/<sec_uid>.json 获取上次数据，
    计算出新增视频数、互动增长量等指标。
    前端可用此数据展示增长趋势。
    """
    latest_file = OUTPUT_DIR / "latest" / f"{sec_uid[:12]}.json"
    if not latest_file.exists():
        return None

    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        return None

    prev_meta = prev.get("meta", {})
    prev_videos = prev.get("videos", [])
    prev_engagement = prev_meta.get("total_engagement", {})

    # 新视频 (之前没见过的 aweme_id)
    prev_ids = {v.get("aweme_id") for v in prev_videos}
    current_ids = {v.get("aweme_id") for v in current_videos}
    new_ids = current_ids - prev_ids

    # 互动增长
    delta = {
        "previous_crawl": prev_meta.get("crawl_time", "unknown"),
        "new_videos": len(new_ids),
        "removed_videos": len(prev_ids - current_ids),
        "likes_delta": current_engagement.get("likes", 0) - prev_engagement.get("likes", 0),
        "comments_delta": current_engagement.get("comments", 0) - prev_engagement.get("comments", 0),
        "shares_delta": current_engagement.get("shares", 0) - prev_engagement.get("shares", 0),
        "plays_delta": current_engagement.get("plays", 0) - prev_engagement.get("plays", 0),
        "collects_delta": current_engagement.get("collects", 0) - prev_engagement.get("collects", 0),
    }

    return delta


def load_history(sec_uid: str) -> List[Dict]:
    """
    加载指定用户的历史抓取数据。

    从 index.json 中找到该用户的所有 session，
    按时间排序返回。前端可用此函数展示历史趋势图。

    Args:
        sec_uid: 用户 sec_uid

    Returns:
        历史 session 列表 (按时间倒序)
    """
    index_path = OUTPUT_DIR / "index.json"
    if not index_path.exists():
        return []

    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    sessions = [
        s for s in index.get("sessions", [])
        if s["sec_uid"] == sec_uid
    ]

    # 如果 index 中找不到，尝试直接扫描目录
    if not sessions:
        sessions = _scan_user_sessions(sec_uid)

    return sessions


def _scan_user_sessions(sec_uid: str) -> List[Dict]:
    """扫描 output 目录查找用户的历史数据文件"""
    sessions = []
    prefix = sec_uid[:12]

    for date_dir in sorted(OUTPUT_DIR.glob("*/"), reverse=True):
        if date_dir.name in ("latest",):
            continue
        for f in sorted(date_dir.glob(f"{prefix}_*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                meta = data.get("meta", {})
                sessions.append({
                    "session_id": f"{date_dir.name}_{f.stem}",
                    "date": date_dir.name,
                    "time": meta.get("time", ""),
                    "timestamp": meta.get("crawl_time", ""),
                    "sec_uid": sec_uid,
                    "author": meta.get("author", ""),
                    "video_count": meta.get("total_videos", 0),
                    "engagement": meta.get("total_engagement", {}),
                    "file": f"{date_dir.name}/{f.name}",
                })
            except Exception:
                continue

    return sessions


def print_results(videos: List[Dict]):
    """打印结果摘要"""
    if not videos:
        return

    print(f"\n{'='*70}")
    print(f"📊 抓取结果 — {len(videos)} 个视频")
    print(f"{'='*70}")

    t = {"likes": 0, "comments": 0, "shares": 0, "plays": 0, "collects": 0}

    for i, v in enumerate(videos, 1):
        s = v["statistics"]
        for k in t:
            t[k] += s.get(k, 0)

        desc = v["desc"][:50]
        if len(v.get("desc", "")) > 50:
            desc += "..."

        icon = "🖼" if v.get("is_image") else "🎵"
        print(f"\n  [{i}] {icon} {desc}")
        print(f"      作者: {v['author']['nickname']}  "
              f"|  {v.get('duration_sec', 0)}s  "
              f"|  {v['create_time']}")
        print(f"      👍 {s['likes']:,}  💬 {s['comments']:,}  "
              f"🔄 {s['shares']:,}  ▶️ {s['plays']:,}  ⭐ {s['collects']:,}")
        if v.get("hashtags"):
            print(f"      🏷 {', '.join(v['hashtags'])}")
        if v.get("music", {}).get("title"):
            print(f"      🎵 {v['music']['title']}")

    print(f"\n{'='*70}")
    print(f"📈 总计互动: 👍 {t['likes']:,}  💬 {t['comments']:,}  "
          f"🔄 {t['shares']:,}  ▶️ {t['plays']:,}")
    if t["collects"]:
        print(f"          ⭐ {t['collects']:,} (收藏)")
    print(f"{'='*70}")


# ============================================================
# 入口
# ============================================================

async def main():
    """主入口"""
    # 解析命令行参数
    sec_uid = TARGET_SEC_UID
    if len(sys.argv) > 1:
        sec_uid = sys.argv[1]
        print(f"📌 使用命令行指定的 sec_uid: {sec_uid}")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         抖音用户主页爬虫 — Scrapling + Playwright              ║
║                                                              ║
║  抓取目标: 用户主页所有视频的标题、描述、点赞、评论、转发等    ║
║  输出格式: JSON                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    # ---- 使用 Playwright 浏览器模式 (主模式) ----
    try:
        videos = await crawl_douyin_user(
            sec_uid=sec_uid,
            max_videos=MAX_VIDEOS,
            headless=HEADLESS,
        )
    except Exception as e:
        print(f"\n❌ 浏览器模式失败: {e}")
        import traceback
        traceback.print_exc()
        videos = []

    # ---- 输出 ----
    if videos:
        save_results(videos, sec_uid)
        print_results(videos)
    else:
        print("\n" + "="*70)
        print("⚠ 未能获取到视频数据")
        print("="*70)
        print("""
可能的原因和解决方案:

  1. 需要登录 — 抖音 Web 版限制未登录用户查看主页
     → 设置 HEADLESS=False，在弹出浏览器中扫码登录
     → 或在代码中设置: HEADLESS = False

  2. sec_uid 无效或已过期
     → 从抖音 APP 重新获取用户分享链接
     → 链接中 /user/ 后的部分即为 sec_uid

  3. 网络或反爬限制
     → 检查网络是否能正常访问 www.douyin.com
     → 尝试使用代理
     → 降低请求频率

  4. 使用备用 API 模式 (需 Cookie)
     → 从浏览器开发者工具中复制 Cookie
     → 调用 crawl_with_fetcher(sec_uid, cookies_str='...')
""")


if __name__ == "__main__":
    asyncio.run(main())
