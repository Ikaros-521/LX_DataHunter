"""Crawl session endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from backend.database import get_db
from backend.models import CrawlSession, MonitoredCreator
from backend.schemas import SessionResponse, SessionListResponse

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

@router.get("", response_model=SessionListResponse)
async def list_sessions(
    creator_id: int | None = None,
    platform: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CrawlSession)
    count_stmt = select(func.count(CrawlSession.id))

    if creator_id:
        stmt = stmt.where(CrawlSession.creator_id == creator_id)
        count_stmt = count_stmt.where(CrawlSession.creator_id == creator_id)
    if platform:
        stmt = stmt.where(CrawlSession.platform == platform)
        count_stmt = count_stmt.where(CrawlSession.platform == platform)
    if status:
        stmt = stmt.where(CrawlSession.status == status)
        count_stmt = count_stmt.where(CrawlSession.status == status)

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(desc(CrawlSession.started_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for s in rows:
        creator = await db.get(MonitoredCreator, s.creator_id)
        items.append(SessionResponse(
            id=s.id,
            creator_id=s.creator_id,
            platform=s.platform,
            trigger_type=s.trigger_type,
            status=s.status,
            error_message=s.error_message,
            started_at=s.started_at,
            finished_at=s.finished_at,
            total_contents=s.total_contents,
            engagement_total=s.engagement_total,
            delta_from_prev=s.delta_from_prev,
            creator_nickname=creator.nickname if creator else "",
        ))
    return {"total": total, "items": items}

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(CrawlSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    creator = await db.get(MonitoredCreator, s.creator_id)
    return SessionResponse(
        id=s.id,
        creator_id=s.creator_id,
        platform=s.platform,
        trigger_type=s.trigger_type,
        status=s.status,
        error_message=s.error_message,
        started_at=s.started_at,
        finished_at=s.finished_at,
        total_contents=s.total_contents,
        engagement_total=s.engagement_total,
        delta_from_prev=s.delta_from_prev,
        creator_nickname=creator.nickname if creator else "",
    )

@router.get("/{session_id}/contents", response_model=dict)
async def get_session_contents(
    session_id: int,
    sort: str = "likes",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    from backend.models import ContentSnapshot
    from backend.schemas import ContentResponse, ContentListResponse

    allowed_sorts = {"likes", "comments", "shares", "plays", "collects", "create_time", "duration_sec"}
    if sort not in allowed_sorts:
        sort = "likes"

    sort_col = getattr(ContentSnapshot, sort)
    if order == "asc":
        sort_col = sort_col.asc()
    else:
        sort_col = sort_col.desc()

    count_stmt = select(func.count(ContentSnapshot.id)).where(ContentSnapshot.session_id == session_id)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(ContentSnapshot)
        .where(ContentSnapshot.session_id == session_id)
        .order_by(sort_col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        ContentResponse(
            id=r.id,
            session_id=r.session_id,
            content_id=r.content_id,
            content_type=r.content_type,
            title=r.title,
            cover_url=r.cover_url,
            create_time=r.create_time,
            duration_sec=r.duration_sec,
            url=r.url,
            tags=r.tags,
            music_title=r.music_title,
            likes=r.likes,
            comments=r.comments,
            shares=r.shares,
            plays=r.plays,
            collects=r.collects,
            extra_data=r.extra_data,
        )
        for r in rows
    ]
    return {"total": total, "items": [i.model_dump() for i in items]}
