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
    Competitor, ResearchComment, ResearchStatus, ResearchTask, beijing_now,
)
from .comment_crawler import (
    CommentItem, crawl_bilibili_comments, crawl_douyin_comments_batch,
    screenshot_pages,
)
from .hot_crawler import _run_coro_in_thread
from .llm_client import (
    LlmClient, build_comment_insight_prompt, build_competitor_content_prompt,
    build_market_overview_prompt,
)
from .platforms import get_adapter
from .platforms.bilibili import _make_credential
from .platforms.base import ContentItem
from .report_builder import build_report, sanitize_report_title

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
        from backend.api.credentials import get_active_cookie

        async with AsyncSessionLocal() as db:
            task = await db.get(ResearchTask, task_id)
            if not task:
                raise ValueError(f"Research task {task_id} not found")
            params = task.params or {}
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
                return

            task.started_at = beijing_now()
            task.error_message = ""
            await self._notify(db, task, ResearchStatus.PENDING.value, "准备调研", 5)

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
                section = await self._collect_competitor(comp, params, cookies.get(comp.platform))
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

            # ---- 阶段四：生成报告 ----
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
            md = build_report(
                title=task.title or "竞品调研",
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
    # 阶段实现
    # ------------------------------------------------------------------

    async def _collect_competitor(self, comp: Competitor, params: dict,
                                  cookie_str: str | None) -> dict:
        """抓取竞品主页信息 + 近期视频列表，构建 section 骨架。"""
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
            "profile_screenshot": "",
            "video_screenshots": [],
            "top_comments": [],
            "content_analysis": "",
            "comment_insight": "",
            "warnings": [],
            # 引擎内部使用（不进报告）
            "_comments": [],
        }
        try:
            adapter = get_adapter(comp.platform)
            kwargs = dict(
                platform_uid=comp.platform_uid,
                max_items=int(params.get("max_videos_per_competitor", 8)),
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
        """按互动量选 Top 视频，爬评论 + 截图，评论落库 ResearchComment。"""
        videos = section.get("videos") or []
        top_k = int(params.get("top_videos_for_comments", 5))
        max_comments = int(params.get("max_comments_per_video", 50))
        if not videos or top_k <= 0:
            if not videos:
                section["warnings"].append("无视频可爬评论（时间范围内无内容或抓取失败）")
            return

        ranked = sorted(videos, key=lambda v: v["likes"] + v["comments"], reverse=True)
        picked = ranked[:top_k]

        all_comments: list[CommentItem] = []
        if comp.platform == "bilibili":
            cred = _make_credential(cookie_str)
            for v in picked:
                try:
                    comments = await crawl_bilibili_comments(
                        int(v["content_id"]), max_comments, credential=cred)
                    for c in comments:
                        c.extra["content_id"] = v["content_id"]
                    all_comments.extend(comments)
                except Exception as e:
                    logger.warning(f"Bilibili comments failed for av{v['content_id']}: {e}")
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


research_engine = ResearchEngine()
