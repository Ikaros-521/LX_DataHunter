"""竞品调研编排引擎：抓取 → 评论 → 截图 → LLM → Markdown 报告。

进度阶段：0-10 准备 → 10-50 逐竞品抓主页+视频列表 → 50-80 评论+截图
→ 80-95 LLM 分析 → 95-100 生成报告落盘。

抖音路径涉及 Playwright，经 _run_coro_in_thread 放入独立线程的
Proactor 事件循环（Windows 下与 uvicorn 的 loop 解耦）；调用处以
run_in_executor 包装，避免阻塞主 loop。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models import (
    Competitor, ResearchComment, ResearchDanmaku, ResearchKeyword,
    ResearchStatus, ResearchTask, beijing_now,
)
from .comment_crawler import (
    CommentItem, crawl_bilibili_comments, crawl_bilibili_danmakus,
    crawl_douyin_comments_batch, screenshot_pages,
)
from .competitor_discovery import search_bilibili_videos, search_douyin_videos
from .hot_crawler import _run_coro_in_thread
from .llm_client import (
    LlmClient, build_comment_insight_prompt, build_competitor_content_prompt,
    build_keyword_content_prompt, build_market_overview_prompt,
    build_video_filter_prompt, parse_keep_indexes,
)
from .platforms import get_adapter
from .platforms.bilibili import _make_credential
from .platforms.base import ContentItem
from .report_builder import build_keyword_report, build_report, sanitize_report_title

logger = logging.getLogger("lx_datahunter.research")

# 阶段进度区间
P_COLLECT_START, P_COLLECT_END = 10.0, 50.0
P_COMMENT_START, P_COMMENT_END = 50.0, 80.0
P_LLM_START, P_LLM_END = 80.0, 95.0

# LLM 输入截断：避免 prompt 超长
MAX_COMMENTS_FOR_LLM = 120
MAX_VIDEO_ROWS_FOR_LLM = 30
OVERVIEW_SUMMARY_CHARS = 800


def _avg(values: list[int]) -> int:
    return sum(values) // len(values) if values else 0


def _videos_table_text(videos: list[dict]) -> str:
    """LLM 输入用的视频列表文本行。"""
    rows = []
    for v in videos[:MAX_VIDEO_ROWS_FOR_LLM]:
        ct = (v.get("create_time") or "")[:10]
        tags = "、".join((v.get("tags") or [])[:4])
        rows.append(
            f"{v.get('title', '')[:50]} | {ct} | 播放{v.get('plays', 0)} | "
            f"赞{v.get('likes', 0)} | 评{v.get('comments', 0)} | "
            f"转{v.get('shares', 0)} | 藏{v.get('collects', 0)} | {tags}"
        )
    return "\n".join(rows)


def _comments_text(comments: list[CommentItem]) -> str:
    """LLM 输入用的评论文本行（按点赞降序截断）。"""
    ordered = sorted(comments, key=lambda c: c.likes, reverse=True)[:MAX_COMMENTS_FOR_LLM]
    rows = []
    for c in ordered:
        loc = c.ip_location or "-"
        rows.append(f"👍{c.likes} | {loc} | {c.user_nickname}: {c.text[:100]}")
    return "\n".join(rows)


def _keyword_videos_table_text(videos: list[dict]) -> str:
    """LLM 输入用的关键词头部视频文本行。"""
    rows = []
    for v in videos[:MAX_VIDEO_ROWS_FOR_LLM]:
        ct = (v.get("create_time") or "")[:10]
        author = (v.get("author_nickname") or "?")[:16]
        rows.append(
            f"{v.get('title', '')[:50]} | {author} | {ct} | "
            f"播放{v.get('plays', 0)} | 赞{v.get('likes', 0)} | 评{v.get('comments', 0)}"
        )
    return "\n".join(rows)


def _keyword_desc(section: dict) -> str:
    from .report_builder import PLATFORM_DISPLAY
    platform = PLATFORM_DISPLAY.get(section.get("platform"), section.get("platform"))
    bits = [f"关键词「{section.get('keyword', '?')}」（{platform}）"]
    if section.get("video_count"):
        bits.append(f"头部视频 {section['video_count']} 条")
    if section.get("avg_plays"):
        bits.append(f"平均播放 {section['avg_plays']}")
    if section.get("publish_span"):
        bits.append(f"发布跨度 {section['publish_span']}")
    authors = section.get("top_authors") or []
    if authors:
        names = "、".join((a.get("nickname") or a.get("uid", "?")) for a in authors[:5])
        bits.append(f"活跃玩家（头部作者）：{names}")
    return "；".join(bits)


def _item_to_video_dict(item: ContentItem) -> dict:
    return {
        "title": item.title,
        "url": item.url,
        "create_time": item.create_time.strftime("%Y-%m-%d") if item.create_time else "",
        "plays": item.plays,
        "likes": item.likes,
        "comments": item.comments,
        "shares": item.shares,
        "collects": item.collects,
        "tags": item.tags or [],
        "content_id": item.content_id,
    }


class ResearchEngine:
    def __init__(self):
        self._broadcast = None
        self._running: set[int] = set()

    def set_broadcast(self, fn):
        self._broadcast = fn

    async def _notify(self, db, task: ResearchTask, status: str, stage: str,
                      progress: float, detail: str = ""):
        task.status = status
        task.stage = stage
        task.progress = round(progress, 1)
        await db.commit()
        if self._broadcast:
            try:
                await self._broadcast({
                    "type": "research_progress",
                    "task_id": task.id,
                    "status": status,
                    "stage": stage,
                    "progress": task.progress,
                    "detail": detail,
                })
            except Exception:
                pass

    def submit(self, task_id: int):
        """异步启动一次调研（不阻塞调用方）。"""
        asyncio.get_running_loop().create_task(self._guarded_run(task_id))

    async def _guarded_run(self, task_id: int):
        if task_id in self._running:
            logger.warning(f"Research task {task_id} already running, skip")
            return
        self._running.add(task_id)
        try:
            await self.run(task_id)
        except Exception as e:
            logger.error(f"Research task {task_id} crashed: {e}", exc_info=True)
            try:
                async with AsyncSessionLocal() as db:
                    task = await db.get(ResearchTask, task_id)
                    if task and task.status not in (
                        ResearchStatus.COMPLETED.value, ResearchStatus.FAILED.value,
                    ):
                        task.status = ResearchStatus.FAILED.value
                        task.error_message = str(e)[:2000]
                        task.finished_at = beijing_now()
                        await db.commit()
            except Exception:
                pass
        finally:
            self._running.discard(task_id)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def run(self, task_id: int):
        async with AsyncSessionLocal() as db:
            task = await db.get(ResearchTask, task_id)
            if not task:
                raise ValueError(f"Research task {task_id} not found")
            params = task.params or {}
            mode = task.mode or "accounts"

            task.started_at = beijing_now()
            task.error_message = ""
            await self._notify(db, task, ResearchStatus.PENDING.value, "准备调研", 5)

            if mode == "keyword":
                sections, market_overview = await self._run_keyword_mode(db, task, params)
                report_fn, default_title = build_keyword_report, "关键词赛道调研"
            else:
                sections, market_overview = await self._run_accounts_mode(db, task, params)
                report_fn, default_title = build_report, "竞品调研"

            # ---- 生成报告 ----
            await self._notify(db, task, ResearchStatus.ANALYZING.value,
                               "生成 Markdown 报告", 98)
            for i, s in enumerate(sections, 1):
                s["index"] = i
            # 报告/JSON 中剔除引擎内部字段（_comments 全量评论等）
            report_sections = [
                {k: v for k, v in s.items() if not k.startswith("_")}
                for s in sections
            ]
            generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            md = report_fn(
                title=task.title or default_title,
                generated_at=generated_at,
                params=params,
                sections=report_sections,
                market_overview=market_overview,
                trigger_type=task.trigger_type,
            )
            md_path = await self._write_report(task, md)

            task.competitor_stats = report_sections
            task.market_overview = market_overview
            task.markdown_path = str(md_path)
            await self._notify(db, task, ResearchStatus.COMPLETED.value,
                               "调研完成", 100)
            logger.info(f"Research task {task_id} completed: {md_path}")

    # ------------------------------------------------------------------
    # 账号分析模式（mode=accounts）
    # ------------------------------------------------------------------

    async def _run_accounts_mode(self, db, task: ResearchTask, params: dict):
        from backend.api.credentials import get_active_cookie

        comp_ids = task.competitor_ids or []
        result = await db.execute(
            select(Competitor).where(Competitor.id.in_(comp_ids))
            .order_by(Competitor.id)
        )
        competitors = result.scalars().all()
        if not competitors:
            task.status = ResearchStatus.FAILED.value
            task.error_message = "No competitors found"
            task.finished_at = beijing_now()
            await db.commit()
            return [], ""

        llm = LlmClient.from_settings()
        llm_ready = bool(settings.llm_base_url and settings.llm_api_key)

        # Cookies（按平台取一次）
        cookies: dict[str, str | None] = {}
        for pf in {c.platform for c in competitors}:
            try:
                cookies[pf] = await get_active_cookie(pf, db)
            except Exception:
                cookies[pf] = None

        sections: list[dict] = []
        n = len(competitors)

        # ---- 阶段一：逐竞品抓主页 + 视频列表 ----
        for i, comp in enumerate(competitors):
            await self._notify(
                db, task, ResearchStatus.COLLECTING.value,
                f"抓取竞品数据（{i + 1}/{n}）：{_comp_label(comp)}",
                P_COLLECT_START + (P_COLLECT_END - P_COLLECT_START) * i / n,
            )
            section = await self._collect_competitor(comp, params, cookies.get(comp.platform),
                                                     llm, llm_ready)
            sections.append(section)
            # 抓完立即回填竞品元数据
            comp.follower_count = section.get("follower_count") or comp.follower_count or 0
            comp.nickname = section.get("nickname") or comp.nickname
            comp.last_researched_at = beijing_now()
            await db.commit()

        # ---- 阶段二：评论区 + 截图 ----
        await self._notify(db, task, ResearchStatus.CRAWLING_COMMENTS.value,
                           "开始爬取评论与截图", P_COMMENT_START)
        for i, (comp, section) in enumerate(zip(competitors, sections)):
            await self._notify(
                db, task, ResearchStatus.CRAWLING_COMMENTS.value,
                f"爬取评论与截图（{i + 1}/{n}）：{_comp_label(comp)}",
                P_COMMENT_START + (P_COMMENT_END - P_COMMENT_START) * i / n,
            )
            await self._collect_comments(db, task, comp, section, params,
                                         cookies.get(comp.platform))

        # ---- 阶段三：LLM 分析 ----
        await self._notify(db, task, ResearchStatus.ANALYZING.value,
                           "LLM 分析中", P_LLM_START)
        for i, (comp, section) in enumerate(zip(competitors, sections)):
            await self._notify(
                db, task, ResearchStatus.ANALYZING.value,
                f"LLM 分析（{i + 1}/{n}）：{_comp_label(comp)}",
                P_LLM_START + (P_LLM_END - P_LLM_START) * i / n,
            )
            await self._analyze_competitor(llm, llm_ready, section)

        market_overview = ""
        if llm_ready and len(sections) > 1:
            try:
                summary = "\n\n".join(_overview_summary(s) for s in sections)
                system, user = build_market_overview_prompt(summary)
                market_overview = await self._chat_with_retry(llm, system, user)
            except Exception as e:
                logger.warning(f"Market overview LLM failed: {e}")
                market_overview = f"*（跨竞品 LLM 分析失败：{e}）*"
        return sections, market_overview

    # ------------------------------------------------------------------
    # 关键词赛道模式（mode=keyword）
    # ------------------------------------------------------------------

    async def _run_keyword_mode(self, db, task: ResearchTask, params: dict):
        from backend.api.credentials import get_active_cookie

        kw_ids = task.keyword_ids or []
        result = await db.execute(
            select(ResearchKeyword).where(
                ResearchKeyword.id.in_(kw_ids), ResearchKeyword.enabled == True)
            .order_by(ResearchKeyword.id)
        )
        keywords = result.scalars().all()
        if not keywords:
            task.status = ResearchStatus.FAILED.value
            task.error_message = "没有可调研的关键词（ids 无效或均已停用）"
            task.finished_at = beijing_now()
            await db.commit()
            return [], ""

        llm = LlmClient.from_settings()
        llm_ready = bool(settings.llm_base_url and settings.llm_api_key)

        cookies: dict[str, str | None] = {}
        for pf in {k.platform for k in keywords}:
            try:
                cookies[pf] = await get_active_cookie(pf, db)
            except Exception:
                cookies[pf] = None

        sections: list[dict] = []
        n = len(keywords)

        # ---- 阶段一：逐关键词搜索头部视频 ----
        for i, kw in enumerate(keywords):
            await self._notify(
                db, task, ResearchStatus.COLLECTING.value,
                f"搜索关键词（{i + 1}/{n}）：{kw.keyword}",
                P_COLLECT_START + (P_COLLECT_END - P_COLLECT_START) * i / n,
            )
            section = await self._collect_keyword(task, kw, params, cookies.get(kw.platform),
                                                  llm, llm_ready)
            sections.append(section)
            kw.last_researched_at = beijing_now()
            kw.extra_info = {
                "video_count": section.get("video_count", 0),
                "avg_plays": section.get("avg_plays", 0),
                "top_authors": [a.get("nickname") or a.get("uid")
                                 for a in (section.get("top_authors") or [])[:5]],
            }
            await db.commit()

        # ---- 阶段二：头部视频评论区 + 截图 ----
        await self._notify(db, task, ResearchStatus.CRAWLING_COMMENTS.value,
                           "开始爬取评论与截图", P_COMMENT_START)
        for i, (kw, section) in enumerate(zip(keywords, sections)):
            await self._notify(
                db, task, ResearchStatus.CRAWLING_COMMENTS.value,
                f"爬取评论与截图（{i + 1}/{n}）：{kw.keyword}",
                P_COMMENT_START + (P_COMMENT_END - P_COMMENT_START) * i / n,
            )
            await self._collect_keyword_comments(db, task, section, params,
                                                 cookies.get(kw.platform))

        # ---- 阶段三：LLM 分析 ----
        await self._notify(db, task, ResearchStatus.ANALYZING.value,
                           "LLM 分析中", P_LLM_START)
        for i, (kw, section) in enumerate(zip(keywords, sections)):
            await self._notify(
                db, task, ResearchStatus.ANALYZING.value,
                f"LLM 分析（{i + 1}/{n}）：{kw.keyword}",
                P_LLM_START + (P_LLM_END - P_LLM_START) * i / n,
            )
            await self._analyze_keyword(llm, llm_ready, section)

        market_overview = ""
        if llm_ready and len(sections) > 1:
            try:
                summary = "\n\n".join(_keyword_overview_summary(s) for s in sections)
                system, user = build_market_overview_prompt(summary)
                market_overview = await self._chat_with_retry(llm, system, user)
            except Exception as e:
                logger.warning(f"Market overview LLM failed: {e}")
                market_overview = f"*（跨赛道 LLM 分析失败：{e}）*"
        return sections, market_overview

    # ------------------------------------------------------------------
    # 阶段实现
    # ------------------------------------------------------------------

    async def _collect_competitor(self, comp: Competitor, params: dict,
                                  cookie_str: str | None,
                                  llm: LlmClient, llm_ready: bool) -> dict:
        """抓取竞品主页信息 + 近期视频列表，构建 section 骨架。

        LLM 筛选开启且配置了竞品备注名时，多抓 3 倍候选（封顶 +20），
        筛掉不相关视频后再截断到目标条数，保证筛选后数量仍然足额。
        """
        section: dict = {
            "competitor_id": comp.id,
            "index": 0,  # build_report 前统一编号
            "name": comp.name or comp.nickname or comp.platform_uid,
            "platform": comp.platform,
            "platform_uid": comp.platform_uid,
            "homepage_url": comp.homepage_url,
            "nickname": comp.nickname,
            "signature": "",
            "follower_count": 0,
            "videos": [],
            "video_count": 0,
            "avg_likes": 0, "avg_comments": 0, "avg_plays": 0,
            "publish_span": "",
            "comment_count": 0,
            "danmaku_count": 0,
            "profile_screenshot": "",
            "video_screenshots": [],
            "top_comments": [],
            "content_analysis": "",
            "comment_insight": "",
            "warnings": [],
            # 引擎内部使用（不进报告）
            "_comments": [],
        }
        max_videos = int(params.get("max_videos_per_competitor", 8))
        focus = (comp.name or "").strip()
        llm_filter_on = bool(params.get("llm_filter_videos", True)) and llm_ready and focus
        try:
            adapter = get_adapter(comp.platform)
            kwargs = dict(
                platform_uid=comp.platform_uid,
                # 筛选开启时多抓候选：筛掉不相关后仍能截满目标条数
                max_items=min(max_videos * 3, max_videos + 20) if llm_filter_on else max_videos,
                headless=True,
                cookie_str=cookie_str,
                date_from=params.get("date_from"),
                date_to=params.get("date_to"),
            )
            if comp.platform == "douyin":
                loop = asyncio.get_running_loop()
                creator_info, items = await loop.run_in_executor(
                    None, _run_coro_in_thread, adapter.crawl_creator(**kwargs))
            else:
                creator_info, items = await adapter.crawl_creator(**kwargs)

            section["nickname"] = creator_info.nickname or comp.nickname
            section["signature"] = creator_info.signature or ""
            section["follower_count"] = creator_info.follower_count or 0
            videos = [_item_to_video_dict(v) for v in items]
            videos.sort(key=lambda v: v["create_time"] or "", reverse=True)
            if llm_filter_on and len(videos) > 1:
                videos = await self._filter_videos_by_llm(llm, focus, videos, section)
                if len(videos) < max_videos:
                    section["warnings"].append(
                        f"LLM 筛选后仅剩 {len(videos)} 条相关视频，不足目标 {max_videos} 条"
                        f"（该账号近期相关内容已用尽）")
                videos = videos[:max_videos]
            section["videos"] = videos
            section["video_count"] = len(videos)
            section["avg_likes"] = _avg([v["likes"] for v in videos])
            section["avg_comments"] = _avg([v["comments"] for v in videos])
            section["avg_plays"] = _avg([v["plays"] for v in videos])
            dates = sorted(v["create_time"] for v in videos if v["create_time"])
            if dates:
                section["publish_span"] = f"{dates[0]} ~ {dates[-1]}"
        except Exception as e:
            logger.warning(f"Collect failed for competitor {comp.id}: {e}", exc_info=True)
            section["warnings"].append(f"主页/视频抓取失败：{e}")
        return section

    async def _collect_comments(self, db, task: ResearchTask, comp: Competitor,
                                section: dict, params: dict, cookie_str: str | None):
        """选视频爬评论 + 截图，评论落库 ResearchComment。

        top_videos_for_comments：-1=全部视频，0=不爬，>0=互动量 Top N；
        max_comments_per_video：-1=全量爬取，>0=每视频上限。
        视频已在采集阶段完成 LLM 相关性筛选，此处不再筛。
        """
        videos = section.get("videos") or []
        top_k = int(params.get("top_videos_for_comments", -1))
        max_comments = int(params.get("max_comments_per_video", 100))
        if not videos or top_k == 0:
            if not videos:
                section["warnings"].append("无视频可爬评论（时间范围内无内容或抓取失败）")
            return

        ranked = sorted(videos, key=lambda v: v["likes"] + v["comments"], reverse=True)
        picked = ranked if top_k < 0 else ranked[:top_k]

        all_comments: list[CommentItem] = []
        if comp.platform == "bilibili":
            cred = _make_credential(cookie_str)
            fails = 0
            for j, v in enumerate(picked):
                try:
                    comments = await crawl_bilibili_comments(
                        int(v["content_id"]), max_comments, credential=cred)
                    for c in comments:
                        c.extra["content_id"] = v["content_id"]
                    all_comments.extend(comments)
                except Exception as e:
                    logger.warning(f"Bilibili comments failed for av{v['content_id']}: {e}")
                    fails += 1
                if j < len(picked) - 1:
                    await asyncio.sleep(1.0)  # 视频间限速，降低连环风控概率
            if fails:
                section["warnings"].append(
                    f"{fails} 个视频评论抓取失败（风控或网络），已跳过")
            # 原始弹幕全量存库（独立于评论，供后续分析复用）
            section["danmaku_count"] = await self._crawl_and_store_danmakus(
                db, task, comp.id, picked, params, cred, section)
            # B站截图（Playwright，独立线程）
            try:
                assets = _assets_dir(task)
                pages = [(section["homepage_url"] or
                          f"https://space.bilibili.com/{comp.platform_uid}",
                          assets / f"comp{comp.id}_profile.png")]
                pages += [(v["url"], assets / f"comp{comp.id}_video_{j + 1}.png")
                          for j, v in enumerate(picked)]
                loop = asyncio.get_running_loop()
                saved = await loop.run_in_executor(
                    None, _run_coro_in_thread, screenshot_pages(
                        pages, cookie_str=cookie_str, cookie_domain=".bilibili.com"))
                if saved:
                    home_url = pages[0][0]
                    if home_url in saved:
                        section["profile_screenshot"] = f"assets/{saved[home_url]}"
                    for j, v in enumerate(picked):
                        if v["url"] in saved:
                            section["video_screenshots"].append({
                                "title": v["title"],
                                "url": v["url"],
                                "screenshot": f"assets/{saved[v['url']]}",
                            })
            except Exception as e:
                logger.warning(f"Bilibili screenshots failed: {e}")
                section["warnings"].append(f"截图失败：{e}")
        elif comp.platform == "douyin":
            if not cookie_str:
                section["warnings"].append(
                    "抖音未配置登录 Cookie，评论与截图已跳过（请在凭证管理中保存抖音 Cookie）")
            else:
                try:
                    profile_url = (section.get("homepage_url") or
                                   f"https://www.douyin.com/user/{comp.platform_uid}")
                    loop = asyncio.get_running_loop()
                    comments_by_url, shots = await loop.run_in_executor(
                        None, _run_coro_in_thread, crawl_douyin_comments_batch(
                            profile_url=profile_url,
                            video_urls=[v["url"] for v in picked],
                            max_per_video=max_comments,
                            cookie_str=cookie_str,
                            screenshot_dir=_assets_dir(task),
                        ))
                    for v in picked:
                        all_comments.extend(comments_by_url.get(v["url"]) or [])
                        if v["url"] in shots:
                            section["video_screenshots"].append({
                                "title": v["title"], "url": v["url"],
                                "screenshot": f"assets/{shots[v['url']]}",
                            })
                    if profile_url in shots:
                        section["profile_screenshot"] = f"assets/{shots[profile_url]}"
                    if not all_comments:
                        section["warnings"].append("抖音评论未抓到（可能触发风控或视频无评论）")
                except Exception as e:
                    logger.warning(f"Douyin comments failed: {e}")
                    section["warnings"].append(f"抖音评论/截图失败：{e}")

        section["_comments"] = all_comments
        section["comment_count"] = len(all_comments)
        section["top_comments"] = [
            {"user_nickname": c.user_nickname, "text": c.text, "likes": c.likes,
             "ip_location": c.ip_location,
             "create_time": c.create_time.strftime("%Y-%m-%d") if c.create_time else ""}
            for c in sorted(all_comments, key=lambda c: c.likes, reverse=True)[:10]
        ]

        # 评论落库（先清掉本任务该竞品的旧记录，支持重跑）
        if all_comments:
            await db.execute(delete(ResearchComment).where(
                ResearchComment.research_task_id == task.id,
                ResearchComment.competitor_id == comp.id,
            ))
            rows = []
            for c in all_comments:
                rows.append(ResearchComment(
                    research_task_id=task.id,
                    competitor_id=comp.id,
                    content_id=str(c.extra.get("content_id", "")),
                    comment_id=c.comment_id,
                    user_nickname=c.user_nickname[:250],
                    text=c.text[:5000],
                    likes=c.likes,
                    create_time=c.create_time,
                    ip_location=c.ip_location[:60],
                    sub_comment_count=c.sub_comment_count,
                ))
            db.add_all(rows)
            await db.commit()

    LLM_FILTER_BATCH = 25

    async def _filter_videos_by_llm(self, llm: LlmClient, focus: str,
                                    videos: list[dict], section: dict) -> list[dict]:
        """按批送 LLM 筛选与调研主题相关的视频（标题+作者昵称）。

        单批失败/解析失败/全不相关时保守保留该批，不阻断；
        剔除总数记入 section["llm_filtered_out"] 供报告展示。
        """
        kept_total: list[dict] = []
        removed = 0
        for start in range(0, len(videos), self.LLM_FILTER_BATCH):
            batch = videos[start:start + self.LLM_FILTER_BATCH]
            titles = [(i, v.get("title") or "", v.get("author_nickname") or "")
                      for i, v in enumerate(batch, 1)]
            try:
                system, user = build_video_filter_prompt(focus, titles)
                text = await self._chat_with_retry(llm, system, user, retries=2)
            except Exception as e:
                logger.warning(f"LLM video filter failed: {e}")
                section["warnings"].append("LLM 视频筛选调用失败，该批视频已保留")
                kept_total.extend(batch)
                continue

            keep = parse_keep_indexes(text, valid={i for i, _, _ in titles})
            if keep is None:
                section["warnings"].append("LLM 视频筛选结果解析失败，该批视频已保留")
                kept_total.extend(batch)
                continue
            if not keep:
                section["warnings"].append("LLM 判定该批视频均与主题不相关，已保守保留")
                kept_total.extend(batch)
                continue
            kept_total.extend(v for i, v in enumerate(batch, 1) if i in keep)
            removed += len(batch) - len(keep)

        if removed:
            logger.info(f"LLM filter: removed {removed}/{len(videos)} videos for '{focus}'")
        section["llm_filtered_out"] = removed
        return kept_total

    async def _crawl_and_store_danmakus(self, db, task: ResearchTask, owner_id: int,
                                        picked: list[dict], params: dict, cred,
                                        section: dict) -> int:
        """B站视频原始弹幕抓取并落库 ResearchDanmaku（重跑先清旧），返回条数。

        owner_id：账号模式为竞品 id，关键词模式为 keyword id。
        弹幕仅作为原始数据存储供后续分析复用，不参与本报告的 LLM 分析。
        单视频失败（-403 权限/风控）不阻断，记入 section warnings。
        """
        if not params.get("crawl_danmaku", True) or not picked:
            return 0
        max_dm = int(params.get("max_danmakus_per_video", -1))
        rows: list[ResearchDanmaku] = []
        fails: list[str] = []
        for j, v in enumerate(picked):
            try:
                dms, err = await crawl_bilibili_danmakus(
                    int(v["content_id"]), max_dm, credential=cred)
                if err:
                    fails.append(f"《{v.get('title', '')[:24]}》{err}")
                rows.extend(ResearchDanmaku(
                    research_task_id=task.id,
                    competitor_id=owner_id,
                    content_id=str(v["content_id"]),
                    danmaku_id=d.danmaku_id,
                    text=d.text[:5000],
                    send_time=d.send_time,
                    dm_time=d.dm_time,
                    mode=d.mode,
                    font_size=d.font_size,
                    color=d.color[:8],
                    user_hash=d.user_hash[:64],
                    pool=d.pool,
                    extra=dict(d.extra, content_id=str(v["content_id"])),
                ) for d in dms)
            except Exception as e:
                logger.warning(f"Bilibili danmaku failed for av{v['content_id']}: {e}")
                fails.append(f"《{v.get('title', '')[:24]}》弹幕抓取异常")
            if j < len(picked) - 1:
                await asyncio.sleep(1.0)  # 视频间限速，降低连环风控概率
        if rows:
            await db.execute(delete(ResearchDanmaku).where(
                ResearchDanmaku.research_task_id == task.id,
                ResearchDanmaku.competitor_id == owner_id,
            ))
            db.add_all(rows)
            await db.commit()
        if fails:
            section["warnings"].append(
                f"{len(fails)} 个视频弹幕抓取失败：{'；'.join(fails[:3])}"
                + ("等" if len(fails) > 3 else ""))
        return len(rows)

    async def _chat_with_retry(self, llm: LlmClient, system: str, user: str,
                               retries: int = 2, delay: float = 8.0) -> str:
        """LLM 调用带重试（调研报告对限流/抖动更宽容）。"""
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                return await llm.chat(system, user)
            except Exception as e:
                last_err = e
                logger.warning(f"LLM call failed (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
        raise last_err  # type: ignore[misc]

    async def _analyze_competitor(self, llm: LlmClient, llm_ready: bool, section: dict):
        """单竞品 LLM 分析（串行 + 重试，避免限流）；失败不阻断。"""
        if not llm_ready:
            section["content_analysis"] = ""
            section["comment_insight"] = ""
            section["warnings"].append("LLM 未配置（LLM_BASE_URL/LLM_API_KEY），分析已跳过")
            return

        creator_desc = _creator_desc(section)

        if section.get("videos"):
            try:
                system, user = build_competitor_content_prompt(
                    creator_desc, _videos_table_text(section["videos"]))
                section["content_analysis"] = await self._chat_with_retry(llm, system, user)
            except Exception as e:
                logger.warning(f"Content analysis LLM failed: {e}")
                section["warnings"].append(f"内容策略分析失败：{e}")
        else:
            section["warnings"].append("无视频数据，内容策略分析跳过")

        if section.get("_comments"):
            try:
                system, user = build_comment_insight_prompt(
                    creator_desc, _comments_text(section["_comments"]))
                section["comment_insight"] = await self._chat_with_retry(llm, system, user)
            except Exception as e:
                logger.warning(f"Comment insight LLM failed: {e}")
                section["warnings"].append(f"评论区洞察分析失败：{e}")
        else:
            section["warnings"].append("无评论数据，评论区洞察跳过")

    async def _collect_keyword(self, task: ResearchTask, kw: ResearchKeyword,
                               params: dict, cookie_str: str | None,
                               llm: LlmClient, llm_ready: bool) -> dict:
        """关键词搜索头部视频，构建赛道 section（含 LLM 相关性筛选）。"""
        section: dict = {
            "keyword_id": kw.id,
            "index": 0,
            "keyword": kw.keyword,
            "platform": kw.platform,
            "videos": [],
            "video_count": 0,
            "avg_plays": 0, "avg_likes": 0, "avg_comments": 0,
            "publish_span": "",
            "top_authors": [],
            "comment_count": 0,
            "danmaku_count": 0,
            "search_screenshot": "",
            "video_screenshots": [],
            "top_comments": [],
            "content_analysis": "",
            "comment_insight": "",
            "warnings": [],
            "_comments": [],
        }
        max_videos = int(params.get("max_videos_per_keyword", 15))
        max_pages = int(params.get("max_search_pages", 2))
        # 候选池放大：搜索按播放量截断后还要过日期过滤，池太小会导致范围内视频数不足
        pool = max(max_videos * 5, 50)

        raw: list[dict] = []
        try:
            if kw.platform == "bilibili":
                raw = await search_bilibili_videos(kw.keyword, pool, max_pages)
            elif kw.platform == "douyin":
                if not cookie_str:
                    section["warnings"].append(
                        "抖音未配置登录 Cookie，搜索已跳过（请在凭证管理中保存抖音 Cookie）")
                    return section
                loop = asyncio.get_running_loop()
                raw, search_shot = await loop.run_in_executor(
                    None, _run_coro_in_thread, search_douyin_videos(
                        kw.keyword, cookie_str=cookie_str,
                        max_videos=pool, max_pages=max_pages,
                        screenshot_dir=_assets_dir(task)))
                if search_shot:
                    section["search_screenshot"] = f"assets/{search_shot}"
            else:
                section["warnings"].append(f"暂不支持平台：{kw.platform}")
                return section
        except Exception as e:
            logger.warning(f"Keyword search failed for '{kw.keyword}': {e}", exc_info=True)
            section["warnings"].append(f"关键词搜索失败：{e}")
            return section

        # 日期过滤（可选）
        date_from, date_to = params.get("date_from"), params.get("date_to")

        def _in_range(ts: int) -> bool:
            if not ts:
                return True
            d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if date_from and d < date_from:
                return False
            if date_to and d > date_to:
                return False
            return True

        if date_from or date_to:
            before = len(raw)
            raw = [v for v in raw if _in_range(v.get("pub_ts", 0))]
            if len(raw) < max_videos:
                section["warnings"].append(
                    f"时间范围内仅命中 {len(raw)} 条视频（候选 {before} 条，"
                    f"可放宽时间范围或加大搜索翻页深度）")

        # LLM 相关性筛选放在截断之前：先剔除不相关候选，再取 Top N，
        # 剔除导致数量不足时自然由池内其余相关视频回补
        if (params.get("llm_filter_videos", True) and llm_ready
                and len(raw) > 1 and kw.keyword):
            raw = await self._filter_videos_by_llm(
                llm, f"关键词「{kw.keyword}」的赛道内容", raw, section)
            if len(raw) < max_videos:
                section["warnings"].append(
                    f"LLM 筛选后仅剩 {len(raw)} 条相关视频，不足目标 {max_videos} 条"
                    f"（相关候选已用尽，可加大搜索翻页深度扩大候选池）")
        raw = raw[:max_videos]

        videos = [{
            "title": v.get("title", ""),
            "url": v.get("url", ""),
            "create_time": (datetime.fromtimestamp(v["pub_ts"]).strftime("%Y-%m-%d")
                             if v.get("pub_ts") else ""),
            "plays": v.get("plays", 0),
            "likes": v.get("likes", 0),
            "comments": v.get("comments", 0),
            "shares": 0, "collects": 0, "tags": [],
            "content_id": v.get("content_id", ""),
            "author_nickname": v.get("author_nickname", ""),
            "author_uid": v.get("author_uid", ""),
            "author_homepage": v.get("author_homepage", ""),
        } for v in raw]

        section["videos"] = videos
        section["video_count"] = len(videos)
        section["avg_plays"] = _avg([v["plays"] for v in videos])
        section["avg_likes"] = _avg([v["likes"] for v in videos])
        section["avg_comments"] = _avg([v["comments"] for v in videos])
        dates = sorted(v["create_time"] for v in videos if v["create_time"])
        if dates:
            section["publish_span"] = f"{dates[0]} ~ {dates[-1]}"

        # 活跃作者聚合（该关键词头部视频中的玩家）
        authors: dict[str, dict] = {}
        for v in videos:
            uid = v.get("author_uid") or v.get("author_nickname")
            if not uid:
                continue
            a = authors.setdefault(uid, {
                "uid": uid, "nickname": v.get("author_nickname", ""),
                "homepage": v.get("author_homepage", ""),
                "video_hits": 0, "total_plays": 0,
            })
            a["video_hits"] += 1
            a["total_plays"] += v.get("plays", 0)
        section["top_authors"] = sorted(
            authors.values(), key=lambda a: (a["video_hits"], a["total_plays"]),
            reverse=True)[:10]
        return section

    async def _collect_keyword_comments(self, db, task: ResearchTask, section: dict,
                                        params: dict, cookie_str: str | None):
        """关键词模式：视频爬评论 + 截图，评论落库。

        top_videos_for_comments：-1=全部视频，0=不爬，>0=互动量 Top N；
        max_comments_per_video：-1=全量爬取，>0=每视频上限。
        视频已在采集阶段完成 LLM 相关性筛选，此处不再筛。
        """
        videos = section.get("videos") or []
        top_k = int(params.get("top_videos_for_comments", -1))
        max_comments = int(params.get("max_comments_per_video", 100))
        if not videos or top_k == 0:
            if not videos:
                section["warnings"].append("关键词下无视频，评论与截图跳过")
            return

        ranked = sorted(videos, key=lambda v: v["likes"] + v["comments"], reverse=True)
        picked = ranked if top_k < 0 else ranked[:top_k]
        all_comments: list[CommentItem] = []
        platform = section.get("platform")

        if platform == "bilibili":
            fails = 0
            for j, v in enumerate(picked):
                try:
                    comments = await crawl_bilibili_comments(
                        int(v["content_id"]), max_comments, credential=_make_credential(cookie_str))
                    for c in comments:
                        c.extra["content_id"] = v["content_id"]
                    all_comments.extend(comments)
                except Exception as e:
                    logger.warning(f"Bilibili comments failed for av{v['content_id']}: {e}")
                    fails += 1
                if j < len(picked) - 1:
                    await asyncio.sleep(1.0)  # 视频间限速，降低连环风控概率
            if fails:
                section["warnings"].append(
                    f"{fails} 个视频评论抓取失败（风控或网络），已跳过")
            section["danmaku_count"] = await self._crawl_and_store_danmakus(
                db, task, section.get("keyword_id", 0), picked, params,
                _make_credential(cookie_str), section)
            try:
                from urllib.parse import quote
                assets = _assets_dir(task)
                pages = [(f"https://search.bilibili.com/all?keyword={quote(section['keyword'])}",
                          assets / f"search_{section['keyword_id']}.png")]
                pages += [(v["url"], assets / f"kw{section['keyword_id']}_video_{j + 1}.png")
                          for j, v in enumerate(picked)]
                loop = asyncio.get_running_loop()
                saved = await loop.run_in_executor(
                    None, _run_coro_in_thread, screenshot_pages(
                        pages, cookie_str=cookie_str, cookie_domain=".bilibili.com"))
                if saved:
                    if pages[0][0] in saved:
                        section["search_screenshot"] = f"assets/{saved[pages[0][0]]}"
                    for v in picked:
                        if v["url"] in saved:
                            section["video_screenshots"].append({
                                "title": v["title"], "url": v["url"],
                                "screenshot": f"assets/{saved[v['url']]}",
                            })
            except Exception as e:
                logger.warning(f"Bilibili keyword screenshots failed: {e}")
                section["warnings"].append(f"截图失败：{e}")
        elif platform == "douyin":
            try:
                loop = asyncio.get_running_loop()
                comments_by_url, shots = await loop.run_in_executor(
                    None, _run_coro_in_thread, crawl_douyin_comments_batch(
                        profile_url=None,
                        video_urls=[v["url"] for v in picked],
                        max_per_video=max_comments,
                        cookie_str=cookie_str,
                        screenshot_dir=_assets_dir(task),
                    ))
                for v in picked:
                    all_comments.extend(comments_by_url.get(v["url"]) or [])
                    if v["url"] in shots:
                        section["video_screenshots"].append({
                            "title": v["title"], "url": v["url"],
                            "screenshot": f"assets/{shots[v['url']]}",
                        })
                if not all_comments:
                    section["warnings"].append("抖音评论未抓到（可能触发风控或视频无评论）")
            except Exception as e:
                logger.warning(f"Douyin keyword comments failed: {e}")
                section["warnings"].append(f"抖音评论/截图失败：{e}")

        section["_comments"] = all_comments
        section["comment_count"] = len(all_comments)
        section["top_comments"] = [
            {"user_nickname": c.user_nickname, "text": c.text, "likes": c.likes,
             "ip_location": c.ip_location,
             "create_time": c.create_time.strftime("%Y-%m-%d") if c.create_time else ""}
            for c in sorted(all_comments, key=lambda c: c.likes, reverse=True)[:10]
        ]

        if all_comments:
            await db.execute(delete(ResearchComment).where(
                ResearchComment.research_task_id == task.id,
                ResearchComment.competitor_id == section.get("keyword_id", 0),
            ))
            rows = [ResearchComment(
                research_task_id=task.id,
                competitor_id=section.get("keyword_id", 0),  # 关键词模式下存 keyword id
                content_id=str(c.extra.get("content_id", "")),
                comment_id=c.comment_id,
                user_nickname=c.user_nickname[:250],
                text=c.text[:5000],
                likes=c.likes,
                create_time=c.create_time,
                ip_location=c.ip_location[:60],
                sub_comment_count=c.sub_comment_count,
            ) for c in all_comments]
            db.add_all(rows)
            await db.commit()

    async def _analyze_keyword(self, llm: LlmClient, llm_ready: bool, section: dict):
        """关键词模式 LLM 分析（串行 + 重试）；失败不阻断。"""
        if not llm_ready:
            section["warnings"].append("LLM 未配置（LLM_BASE_URL/LLM_API_KEY），分析已跳过")
            return

        desc = _keyword_desc(section)
        if section.get("videos"):
            try:
                authors_text = "\n".join(
                    f"{(a.get('nickname') or a.get('uid', '?'))[:20]} | "
                    f"入榜{a['video_hits']}条 | 播放合计{a['total_plays']}"
                    for a in (section.get("top_authors") or [])[:10])
                system, user = build_keyword_content_prompt(
                    desc, _keyword_videos_table_text(section["videos"]), authors_text)
                section["content_analysis"] = await self._chat_with_retry(llm, system, user)
            except Exception as e:
                logger.warning(f"Keyword content LLM failed: {e}")
                section["warnings"].append(f"赛道内容分析失败：{e}")
        else:
            section["warnings"].append("无视频数据，赛道内容分析跳过")

        if section.get("_comments"):
            try:
                system, user = build_comment_insight_prompt(
                    desc, _comments_text(section["_comments"]))
                section["comment_insight"] = await self._chat_with_retry(llm, system, user)
            except Exception as e:
                logger.warning(f"Keyword comment insight LLM failed: {e}")
                section["warnings"].append(f"用户评论洞察失败：{e}")
        else:
            section["warnings"].append("无评论数据，用户评论洞察跳过")

    async def _write_report(self, task: ResearchTask, md: str) -> Path:
        task_dir = settings.reports_dir / f"{task.id}_{sanitize_report_title(task.title)}"
        task_dir.mkdir(parents=True, exist_ok=True)
        out = task_dir / "report.md"
        out.write_text(md, encoding="utf-8")
        return out


def _assets_dir(task: ResearchTask) -> Path:
    d = settings.reports_dir / f"{task.id}_{sanitize_report_title(task.title)}" / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _comp_label(comp: Competitor) -> str:
    return comp.name or comp.nickname or comp.platform_uid


def _creator_desc(section: dict) -> str:
    from .report_builder import PLATFORM_DISPLAY
    platform = PLATFORM_DISPLAY.get(section.get("platform"), section.get("platform"))
    bits = [f"{section.get('name') or '?'}（{platform}）"]
    if section.get("nickname"):
        bits.append(f"平台昵称：{section['nickname']}")
    if section.get("follower_count"):
        bits.append(f"粉丝：{section['follower_count']}")
    if section.get("signature"):
        bits.append(f"签名：{section['signature'][:100]}")
    if section.get("publish_span"):
        bits.append(f"近期发布跨度：{section['publish_span']}（{section.get('video_count', 0)} 条）")
    return "；".join(bits)


def _overview_summary(section: dict) -> str:
    """跨竞品 LLM 的单竞品摘要。"""
    lines = [f"### {_comp_label_from_section(section)}"]
    lines.append(
        f"平台 {section.get('platform')}；粉丝 {section.get('follower_count', 0)}；"
        f"近期视频 {section.get('video_count', 0)} 条；平均点赞 {section.get('avg_likes', 0)}、"
        f"平均评论 {section.get('avg_comments', 0)}、平均播放 {section.get('avg_plays', 0)}"
    )
    if section.get("content_analysis"):
        lines.append("内容策略分析：\n" + section["content_analysis"][:OVERVIEW_SUMMARY_CHARS])
    if section.get("comment_insight"):
        lines.append("评论区洞察：\n" + section["comment_insight"][:OVERVIEW_SUMMARY_CHARS])
    for w in section.get("warnings") or []:
        lines.append(f"（注意：{w}）")
    return "\n".join(lines)


def _comp_label_from_section(section: dict) -> str:
    return section.get("name") or section.get("nickname") or section.get("platform_uid", "?")


def _keyword_overview_summary(section: dict) -> str:
    """跨赛道 LLM 的单关键词摘要。"""
    lines = [f"### 关键词「{section.get('keyword', '?')}」（{section.get('platform')}）"]
    lines.append(
        f"头部视频 {section.get('video_count', 0)} 条；平均播放 {section.get('avg_plays', 0)}、"
        f"平均点赞 {section.get('avg_likes', 0)}、平均评论 {section.get('avg_comments', 0)}；"
        f"抓取评论 {section.get('comment_count', 0)} 条"
    )
    authors = section.get("top_authors") or []
    if authors:
        lines.append("活跃玩家：" + "、".join(
            (a.get("nickname") or a.get("uid", "?")) for a in authors[:5]))
    if section.get("content_analysis"):
        lines.append("赛道内容分析：\n" + section["content_analysis"][:OVERVIEW_SUMMARY_CHARS])
    if section.get("comment_insight"):
        lines.append("用户评论洞察：\n" + section["comment_insight"][:OVERVIEW_SUMMARY_CHARS])
    for w in section.get("warnings") or []:
        lines.append(f"（注意：{w}）")
    return "\n".join(lines)


research_engine = ResearchEngine()
