"""Schedule configuration endpoints."""
import logging
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger("fapai.schedules")
from backend.database import get_db
from backend.models import ScheduleConfig, MonitoredCreator, beijing_now
from backend.schemas import ScheduleUpsert, ScheduleResponse

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ScheduleConfig))).scalars().all()
    return [ScheduleResponse(
        id=r.id, creator_id=r.creator_id,
        interval_minutes=r.interval_minutes, enabled=r.enabled,
        last_run_at=r.last_run_at, next_run_at=r.next_run_at,
        extra_config=r.extra_config,
    ) for r in rows]

@router.put("/{creator_id}", response_model=ScheduleResponse)
async def upsert_schedule(
    creator_id: int,
    body: ScheduleUpsert,
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")

    existing = (await db.execute(
        select(ScheduleConfig).where(ScheduleConfig.creator_id == creator_id)
    )).scalar_one_or_none()

    extra_config = {"max_items": body.max_items}
    if body.keyword:
        extra_config["keyword"] = body.keyword

    if existing:
        existing.interval_minutes = body.interval_minutes
        existing.enabled = body.enabled
        existing.extra_config = extra_config
        existing.next_run_at = beijing_now() + timedelta(minutes=body.interval_minutes)
        schedule = existing
    else:
        schedule = ScheduleConfig(
            creator_id=creator_id,
            interval_minutes=body.interval_minutes,
            enabled=body.enabled,
            extra_config=extra_config,
            next_run_at=beijing_now() + timedelta(minutes=body.interval_minutes),
        )
        db.add(schedule)

    await db.commit()
    await db.refresh(schedule)
    return ScheduleResponse(
        id=schedule.id, creator_id=schedule.creator_id,
        interval_minutes=schedule.interval_minutes, enabled=schedule.enabled,
        last_run_at=schedule.last_run_at, next_run_at=schedule.next_run_at,
        extra_config=schedule.extra_config,
    )

@router.delete("/{creator_id}")
async def delete_schedule(creator_id: int, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(
        select(ScheduleConfig).where(ScheduleConfig.creator_id == creator_id)
    )).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()
    return {"message": "Deleted"}
