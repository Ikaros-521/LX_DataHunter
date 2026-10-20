"""Cross-creator content search."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.database import get_db
from backend.models import (
    ContentSnapshot, CrawlSession, MonitoredCreator, SessionStatus,
    DownloadTask, DownloadStatus, VideoAnalysis,
)
from backend.schemas import ContentResponse, ContentListResponse

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

# 同一视频存在多条下载任务时，状态展示优先级：已完成 > 进行中 > 失败
_DL_STATUS_PRIORITY = {
    DownloadStatus.COMPLETED.value: 3,
    DownloadStatus.DOWNLOADING.value: 2,
    DownloadStatus.PENDING.value: 1,
    DownloadStatus.FAILED.value: 0,
}


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

    # session / creator 信息
    session_map: dict[int, CrawlSession] = {}
    creator_names: dict[int, str] = {}
    for r in rows:
        if r.session_id not in session_map:
            session_map[r.session_id] = await db.get(CrawlSession, r.session_id)
    for s in session_map.values():
        if s and s.creator_id not in creator_names:
            creator = await db.get(MonitoredCreator, s.creator_id)
            creator_names[s.creator_id] = creator.nickname if creator else ""

    items = []
    for r in rows:
        session = session_map.get(r.session_id)
        items.append({
            "id": r.id, "session_id": r.session_id, "content_id": r.content_id,
            "content_type": r.content_type, "title": r.title, "cover_url": r.cover_url,
            "create_time": r.create_time, "duration_sec": r.duration_sec, "url": r.url,
            "tags": r.tags, "music_title": r.music_title,
            "likes": r.likes, "comments": r.comments, "shares": r.shares,
            "plays": r.plays, "collects": r.collects, "extra_data": r.extra_data,
            "platform": session.platform if session else "",
            "creator_nickname": creator_names.get(session.creator_id, "") if session else "",
        })

    # 按 (platform, content_id) 跨快照匹配下载/分析状态：同视频被多次爬取产生
    # 多份快照时，任意一份已下载/已分析即视为已复用，避免重复下载。
    pairs = {(i["platform"], i["content_id"]) for i in items}
    content_ids = {cid for _, cid in pairs}
    if content_ids:
        dl_rows = (
            await db.execute(
                select(DownloadTask, VideoAnalysis)
                .outerjoin(VideoAnalysis, VideoAnalysis.download_task_id == DownloadTask.id)
                .where(DownloadTask.content_id.in_(content_ids))
            )
        ).all()
        best: dict[tuple[str, str], tuple] = {}
        for task, analysis in dl_rows:
            key = (task.platform, task.content_id)
            if key not in pairs:
                continue
            cur = best.get(key)
            if cur is None or _DL_STATUS_PRIORITY.get(task.status, -1) >= _DL_STATUS_PRIORITY.get(cur[0].status, -1):
                best[key] = (task, analysis)
        for i in items:
            entry = best.get((i["platform"], i["content_id"]))
            if not entry:
                continue
            task, analysis = entry
            i["download_task_id"] = task.id
            i["download_status"] = task.status
            if analysis:
                i["analysis_id"] = analysis.id
                i["analysis_status"] = analysis.status

    return {"total": total, "items": [ContentResponse(**i) for i in items]}
