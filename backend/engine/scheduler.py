"""APScheduler lifecycle management."""
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import ScheduleConfig, MonitoredCreator, CrawlSession, SessionStatus, TriggerType
from .crawler import crawl_creator

scheduler = AsyncIOScheduler()


async def run_scheduled_check():
    """Check for due schedules and execute them."""
    async with AsyncSessionLocal() as db:
        now = datetime.utcnow()
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
                await crawl_creator(
                    creator=creator,
                    db=db,
                    trigger_type=TriggerType.SCHEDULED,
                )
            except Exception:
                pass
            finally:
                schedule.last_run_at = datetime.utcnow()
                schedule.next_run_at = datetime.utcnow() + timedelta(minutes=schedule.interval_minutes)
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
