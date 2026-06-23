"""Cross-creator content search."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from backend.database import get_db
from backend.models import ContentSnapshot, CrawlSession, MonitoredCreator, SessionStatus
from backend.schemas import ContentResponse, ContentListResponse

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

@router.get("", response_model=ContentListResponse)
async def search_contents(
    keyword: str = "",
    creator_id: int | None = None,
    platform: str | None = None,
    tag: str | None = None,
    content_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "likes",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ContentSnapshot)
        .join(CrawlSession, ContentSnapshot.session_id == CrawlSession.id)
        .where(CrawlSession.status == SessionStatus.COMPLETED.value)
    )
    count_stmt = (
        select(func.count(ContentSnapshot.id))
        .join(CrawlSession, ContentSnapshot.session_id == CrawlSession.id)
        .where(CrawlSession.status == SessionStatus.COMPLETED.value)
    )

    if keyword:
        stmt = stmt.where(ContentSnapshot.title.ilike(f"%{keyword}%"))
        count_stmt = count_stmt.where(ContentSnapshot.title.ilike(f"%{keyword}%"))
    if creator_id:
        stmt = stmt.where(CrawlSession.creator_id == creator_id)
        count_stmt = count_stmt.where(CrawlSession.creator_id == creator_id)
    if platform:
        stmt = stmt.where(CrawlSession.platform == platform)
        count_stmt = count_stmt.where(CrawlSession.platform == platform)
    if content_type:
        stmt = stmt.where(ContentSnapshot.content_type == content_type)
        count_stmt = count_stmt.where(ContentSnapshot.content_type == content_type)
    if tag:
        stmt = stmt.where(ContentSnapshot.tags.ilike(f"%{tag}%"))
        count_stmt = count_stmt.where(ContentSnapshot.tags.ilike(f"%{tag}%"))
    if date_from:
        stmt = stmt.where(ContentSnapshot.create_time >= date_from)
        count_stmt = count_stmt.where(ContentSnapshot.create_time >= date_from)
    if date_to:
        stmt = stmt.where(ContentSnapshot.create_time <= date_to)
        count_stmt = count_stmt.where(ContentSnapshot.create_time <= date_to)

    total = (await db.execute(count_stmt)).scalar() or 0

    allowed_sorts = {"likes", "comments", "shares", "plays", "collects", "create_time"}
    if sort not in allowed_sorts:
        sort = "likes"
    sort_col = getattr(ContentSnapshot, sort)
    sort_col = sort_col.desc() if order == "desc" else sort_col.asc()

    stmt = stmt.order_by(sort_col).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for r in rows:
        session = await db.get(CrawlSession, r.session_id)
        creator = await db.get(MonitoredCreator, session.creator_id) if session else None
        items.append(ContentResponse(
            id=r.id, session_id=r.session_id, content_id=r.content_id,
            content_type=r.content_type, title=r.title, cover_url=r.cover_url,
            create_time=r.create_time, duration_sec=r.duration_sec, url=r.url,
            tags=r.tags, music_title=r.music_title,
            likes=r.likes, comments=r.comments, shares=r.shares,
            plays=r.plays, collects=r.collects, extra_data=r.extra_data,
            platform=session.platform if session else "",
            creator_nickname=creator.nickname if creator else "",
        ))
    return {"total": total, "items": items}
