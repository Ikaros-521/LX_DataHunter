"""Creator CRUD + trigger crawl."""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from backend.database import get_db
from backend.models import MonitoredCreator, CrawlSession, SessionStatus, TriggerType
from backend.schemas import (
    CreatorCreate, CreatorResponse, CreatorListResponse, TriggerCrawlResponse, CrawlOptions,
)
from backend.engine.crawler import crawl_creator

logger = logging.getLogger("fapai.creators")
router = APIRouter(prefix="/api/v1/creators", tags=["creators"])

async def _to_response(creator: MonitoredCreator, db: AsyncSession) -> dict:
    """Build CreatorResponse with latest stats."""
    latest_stmt = (
        select(CrawlSession)
        .where(
            CrawlSession.creator_id == creator.id,
            CrawlSession.status == SessionStatus.COMPLETED.value,
        )
        .order_by(desc(CrawlSession.finished_at))
        .limit(1)
    )
    result = await db.execute(latest_stmt)
    latest = result.scalar_one_or_none()

    data = {
        "id": creator.id,
        "platform": creator.platform,
        "platform_uid": creator.platform_uid,
        "nickname": creator.nickname,
        "signature": creator.signature,
        "avatar_url": creator.avatar_url,
        "follower_count": creator.follower_count,
        "extra_info": creator.extra_info,
        "created_at": creator.created_at,
        "updated_at": creator.updated_at,
        "latest_stats": None,
        "schedule": None,
    }
    if latest:
        data["latest_stats"] = {
            "total_contents": latest.total_contents,
            "engagement": latest.engagement_total,
            "fetched_at": latest.finished_at,
        }
    if creator.schedule:
        data["schedule"] = {
            "id": creator.schedule.id,
            "creator_id": creator.schedule.creator_id,
            "interval_minutes": creator.schedule.interval_minutes,
            "enabled": creator.schedule.enabled,
            "last_run_at": creator.schedule.last_run_at,
            "next_run_at": creator.schedule.next_run_at,
        }
    return data

@router.post("", response_model=CreatorResponse, status_code=201)
async def add_creator(body: CreatorCreate, db: AsyncSession = Depends(get_db)):
    logger.info(f"Adding creator: platform={body.platform.value}, uid={body.platform_uid}")

    # Check duplicate
    existing = await db.execute(
        select(MonitoredCreator).where(
            MonitoredCreator.platform == body.platform.value,
            MonitoredCreator.platform_uid == body.platform_uid,
        )
    )
    dup = existing.scalar_one_or_none()
    if dup:
        detail = (
            f"Creator already exists: "
            f"platform={body.platform.value}, "
            f"uid={body.platform_uid}, "
            f"existing_id={dup.id}, "
            f"existing_nickname='{dup.nickname or '(not crawled yet)'}'"
        )
        logger.warning(f"409 Conflict: {detail}")
        raise HTTPException(status_code=409, detail=detail)

    creator = MonitoredCreator(
        platform=body.platform.value,
        platform_uid=body.platform_uid,
    )
    db.add(creator)
    await db.commit()
    await db.refresh(creator, ["schedule"])
    logger.info(f"Creator created: id={creator.id}")
    return await _to_response(creator, db)

@router.get("", response_model=CreatorListResponse)
async def list_creators(
    platform: str | None = None,
    search: str = "",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MonitoredCreator).options(selectinload(MonitoredCreator.schedule))
    count_stmt = select(func.count(MonitoredCreator.id))

    if platform:
        stmt = stmt.where(MonitoredCreator.platform == platform)
        count_stmt = count_stmt.where(MonitoredCreator.platform == platform)
    if search:
        stmt = stmt.where(MonitoredCreator.nickname.ilike(f"%{search}%"))
        count_stmt = count_stmt.where(MonitoredCreator.nickname.ilike(f"%{search}%"))

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(desc(MonitoredCreator.updated_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = [await _to_response(c, db) for c in rows]
    return {"total": total, "items": items}

@router.get("/{creator_id}", response_model=CreatorResponse)
async def get_creator(creator_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(MonitoredCreator).options(selectinload(MonitoredCreator.schedule)).where(MonitoredCreator.id == creator_id)
    result = await db.execute(stmt)
    creator = result.scalar_one_or_none()
    if not creator:
        raise HTTPException(404, "Creator not found")
    return await _to_response(creator, db)

@router.delete("/{creator_id}")
async def delete_creator(creator_id: int, db: AsyncSession = Depends(get_db)):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, f"Creator not found: id={creator_id}")
    logger.info(f"Deleting creator: id={creator.id}, platform={creator.platform}, uid={creator.platform_uid}")
    await db.delete(creator)
    await db.commit()
    return {"message": f"Deleted creator id={creator_id}"}

@router.post("/{creator_id}/crawl", response_model=TriggerCrawlResponse, status_code=202)
async def trigger_crawl(
    creator_id: int,
    background_tasks: BackgroundTasks,
    options: CrawlOptions = CrawlOptions(),
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, f"Creator not found: id={creator_id}")

    # Check not already running
    running_check = await db.execute(
        select(CrawlSession).where(
            CrawlSession.creator_id == creator.id,
            CrawlSession.status == SessionStatus.RUNNING.value,
        )
    )
    if running_check.scalar_one_or_none():
        raise HTTPException(409, f"Creator id={creator_id} already has a running crawl")

    # Create session immediately
    session = CrawlSession(
        creator_id=creator.id,
        platform=creator.platform,
        trigger_type=TriggerType.MANUAL.value,
        status=SessionStatus.RUNNING.value,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    logger.info(f"Crawl triggered: creator_id={creator.id}, session_id={session.id}, "
                f"max={options.max_items}, date_from={options.date_from}, date_to={options.date_to}")

    # Run crawl in background
    background_tasks.add_task(_run_crawl, creator, session.id, options)

    return {"session_id": session.id, "message": "Crawl started"}

async def _run_crawl(creator: MonitoredCreator, session_id: int, options: CrawlOptions = CrawlOptions()):
    """Background task wrapper with WebSocket progress broadcasting."""
    from backend.database import AsyncSessionLocal
    from backend.api.ws import broadcast_progress

    logger.info(f"Background crawl starting: session_id={session_id}, "
                f"max_items={options.max_items}, date_from={options.date_from}, date_to={options.date_to}")

    async def on_progress(current: int, total: int, status: str):
        await broadcast_progress(session_id, current, total, status)

    async with AsyncSessionLocal() as db:
        c = await db.get(MonitoredCreator, creator.id)
        if not c:
            logger.error(f"Background crawl failed: creator id={creator.id} not found")
            return
        try:
            await crawl_creator(
                creator=c,
                db=db,
                trigger_type=TriggerType.MANUAL,
                max_items=options.max_items or 50,
                progress_callback=on_progress,
                existing_session_id=session_id,
                date_from=options.date_from,
                date_to=options.date_to,
            )
            logger.info(f"Background crawl completed: session_id={session_id}")
        except Exception as e:
            logger.error(f"Background crawl failed: session_id={session_id}, error={e}", exc_info=True)
            await broadcast_progress(session_id, 0, 0, "failed")
