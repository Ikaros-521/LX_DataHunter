"""Crawl session endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, delete
from backend.database import get_db

logger = logging.getLogger("fapai.sessions")
from backend.models import CrawlSession, MonitoredCreator, ContentSnapshot
from backend.schemas import SessionResponse, SessionListResponse, BatchDeleteRequest

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

@router.get("", response_model=SessionListResponse)
async def list_sessions(
    creator_id: int | None = None,
    platform: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
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
    if keyword:
        stmt = stmt.where(CrawlSession.keyword == keyword)
        count_stmt = count_stmt.where(CrawlSession.keyword == keyword)

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
            keyword=s.keyword or "",
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
        keyword=s.keyword or "",
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


@router.delete("/cleanup/all")
async def clear_all_sessions(db: AsyncSession = Depends(get_db)):
    """全局清除所有爬取数据（session + content），保留创作者和定时任务。"""
    stmt = select(CrawlSession).where(CrawlSession.status != "running")
    rows = (await db.execute(stmt)).scalars().all()
    count = len(rows)
    for s in rows:
        await db.delete(s)
    if count > 0:
        await db.commit()
        logger.info(f"Cleared all {count} sessions globally")
    return {"message": f"Cleared {count} sessions total"}


@router.delete("/batch")
async def batch_delete_sessions(
    body: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量删除指定的爬取session，跳过running状态的session。"""
    session_ids = body.session_ids
    deleted = 0
    skipped = 0
    for sid in session_ids:
        session = await db.get(CrawlSession, sid)
        if not session:
            continue
        if session.status == "running":
            skipped += 1
            continue
        await db.delete(session)
        deleted += 1
    if deleted > 0:
        await db.commit()
        logger.info(f"Batch deleted {deleted} sessions, skipped {skipped} running")
    return {"message": f"Deleted {deleted} sessions, skipped {skipped} running"}


@router.delete("/creator/{creator_id}")
async def clear_creator_sessions(creator_id: int, db: AsyncSession = Depends(get_db)):
    """清除某创作者的所有爬取数据（session + content），保留创作者和定时任务。"""
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, f"Creator not found: id={creator_id}")
    stmt = select(CrawlSession).where(
        CrawlSession.creator_id == creator_id,
        CrawlSession.status != "running",
    )
    rows = (await db.execute(stmt)).scalars().all()
    count = len(rows)
    for s in rows:
        await db.delete(s)
    if count > 0:
        await db.commit()
        logger.info(f"Cleared {count} sessions for creator_id={creator_id}")
    return {"message": f"Cleared {count} sessions for creator {creator_id}"}


@router.delete("/{session_id}")
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """删除单个爬取session及其级联的content数据。不允许删除running状态的session。"""
    session = await db.get(CrawlSession, session_id)
    if not session:
        raise HTTPException(404, f"Session not found: id={session_id}")
    if session.status == "running":
        raise HTTPException(400, "Cannot delete a running session")
    logger.info(f"Deleting session: id={session.id}, creator_id={session.creator_id}")
    await db.delete(session)
    await db.commit()
    return {"message": f"Deleted session id={session_id}"}
