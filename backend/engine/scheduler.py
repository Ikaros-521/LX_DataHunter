"""APScheduler lifecycle management."""
import logging
from datetime import timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import ScheduleConfig, MonitoredCreator, CrawlSession, SessionStatus, TriggerType, beijing_now

logger = logging.getLogger("lx_datahunter.scheduler")
scheduler = AsyncIOScheduler()


async def run_scheduled_research():
    """检查到期的竞品调研定时配置并触发（每个 tick 至多一次）。"""
    from backend.models import Competitor, ResearchKeyword, ResearchSchedule, ResearchTask
    from backend.api.research import start_research

    async with AsyncSessionLocal() as db:
        sched = (await db.execute(
            select(ResearchSchedule).where(ResearchSchedule.enabled == True).limit(1)
        )).scalar_one_or_none()
        if not sched or not sched.next_run_at or sched.next_run_at > beijing_now():
            return

        # 有正在运行的调研则顺延
        running = (await db.execute(
            select(ResearchTask.id).where(
                ResearchTask.status.notin_(["completed", "failed"]))
        )).scalars().first()
        if running:
            sched.next_run_at = beijing_now() + timedelta(minutes=30)
            await db.commit()
            return

        cfg = sched.params or {}
        days = int(cfg.get("days", 30))
        mode = cfg.get("mode", "accounts")
        params = {
            "mode": mode,
            "date_from": (beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d"),
            "date_to": None,
            "max_videos_per_competitor": int(cfg.get("max_videos_per_competitor", 8)),
            "top_videos_for_comments": int(cfg.get("top_videos_for_comments", -1)),
            "max_comments_per_video": int(cfg.get("max_comments_per_video", 100)),
            "max_videos_per_keyword": int(cfg.get("max_videos_per_keyword", 15)),
            "max_search_pages": int(cfg.get("max_search_pages", 2)),
            "crawl_danmaku": bool(cfg.get("crawl_danmaku", True)),
            "max_danmakus_per_video": int(cfg.get("max_danmakus_per_video", -1)),
            "llm_filter_videos": bool(cfg.get("llm_filter_videos", True)),
        }
        if mode == "keyword":
            ids = (await db.execute(
                select(ResearchKeyword.id).where(ResearchKeyword.enabled == True)
            )).scalars().all()
        else:
            ids = (await db.execute(
                select(Competitor.id).where(Competitor.enabled == True)
            )).scalars().all()

        try:
            if ids:
                await start_research(
                    db, list(ids), params,
                    title=cfg.get("title_prefix", "定时竞品调研") +
                          beijing_now().strftime(" %Y%m%d %H:%M"),
                    trigger_type="scheduled",
                    mode=mode,
                )
        except Exception as e:
            logger.error(f"Scheduled research failed to start: {e}")
        finally:
            sched.last_run_at = beijing_now()
            sched.next_run_at = beijing_now() + timedelta(minutes=sched.interval_minutes)
            await db.commit()


async def run_scheduled_check():
    """Check for due schedules and execute them."""
    # 竞品调研定时（独立配置表）
    try:
        await run_scheduled_research()
    except Exception as e:
        logger.error(f"Scheduled research check failed: {e}", exc_info=True)

    async with AsyncSessionLocal() as db:
        now = beijing_now()
        result = await db.execute(
            select(ScheduleConfig).where(
                ScheduleConfig.enabled == True,
                ScheduleConfig.next_run_at <= now,
            ).limit(5)
        )
        due = result.scalars().all()

        for schedule in due:
            creator = await db.get(MonitoredCreator, schedule.creator_id)
            if not creator:
                continue

            # Check if already running
            running_check = await db.execute(
                select(CrawlSession).where(
                    CrawlSession.creator_id == creator.id,
                    CrawlSession.status == SessionStatus.RUNNING.value,
                )
            )
            if running_check.scalar_one_or_none():
                continue

            try:
                cfg = schedule.extra_config or {}
                keyword = cfg.get("keyword") if cfg else None
                max_items = cfg.get("max_items", 50) if cfg else 50
                await crawl_creator(
                    creator=creator,
                    db=db,
                    trigger_type=TriggerType.SCHEDULED,
                    keyword=keyword,
                    max_items=max_items,
                )
            except Exception:
                pass
            finally:
                # 刷新 schedule 以获取可能在爬取期间被 API 修改的 interval_minutes
                await db.refresh(schedule)
                schedule.last_run_at = beijing_now()
                schedule.next_run_at = beijing_now() + timedelta(minutes=schedule.interval_minutes)
                await db.commit()


def start_scheduler():
    scheduler.add_job(
        run_scheduled_check,
        trigger=IntervalTrigger(minutes=1),
        id="schedule_checker",
        name="Check due schedules every minute",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler():
    scheduler.shutdown(wait=False)
