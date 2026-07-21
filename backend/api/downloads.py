"""视频下载任务管理 API。"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import (
    DownloadTask, DownloadStatus, ContentSnapshot, CrawlSession,
)
from backend.schemas import (
    DownloadBatchCreate, DownloadTaskResponse, DownloadListResponse,
    DownloadBatchDeleteRequest, DownloadStatsResponse, YtdlpCheckResponse,
    MessageResponse,
)
from backend.engine.downloader import download_engine

logger = logging.getLogger("lx_datahunter.api.downloads")
router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])


def _task_to_response(task: DownloadTask, url: str = "") -> DownloadTaskResponse:
    """将 ORM 对象转换为响应 schema。"""
    return DownloadTaskResponse(
        id=task.id,
        content_snapshot_id=task.content_snapshot_id,
        platform=task.platform,
        content_id=task.content_id,
        title=task.title,
        status=task.status,
        progress=task.progress,
        file_path=task.file_path,
        file_size=task.file_size,
        error_message=task.error_message,
        retry_count=task.retry_count,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        url=url,
    )


# ---- 检测 yt-dlp ----

@router.get("/check-ytdlp", response_model=YtdlpCheckResponse)
async def check_ytdlp():
    """检测 yt-dlp 是否可用。"""
    info = await download_engine.check_ytdlp()
    return YtdlpCheckResponse(**info)


# ---- 批量创建下载任务 ----

@router.post("/batch", status_code=202)
async def batch_create_downloads(
    body: DownloadBatchCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """批量创建下载任务，仅接受 video 类型且未下载过的内容。"""
    if not download_engine.is_available:
        raise HTTPException(
            503,
            "yt-dlp 未安装或不可用，请先安装: pip install yt-dlp",
        )

    # 查询内容快照（含关联 session 的 platform）
    result = await db.execute(
        select(ContentSnapshot, CrawlSession.platform)
        .join(CrawlSession, ContentSnapshot.session_id == CrawlSession.id)
        .where(ContentSnapshot.id.in_(body.content_ids))
    )
    rows = result.all()  # list of (ContentSnapshot, platform)
    snapshot_map: dict[int, tuple[ContentSnapshot, str]] = {r[0].id: (r[0], r[1]) for r in rows}

    # 查询已有任务
    existing_result = await db.execute(
        select(DownloadTask.content_snapshot_id).where(
            DownloadTask.content_snapshot_id.in_(body.content_ids),
            DownloadTask.status.in_([
                DownloadStatus.PENDING.value,
                DownloadStatus.DOWNLOADING.value,
                DownloadStatus.COMPLETED.value,
            ]),
        )
    )
    existing_ids = set(existing_result.scalars().all())

    created = 0
    skipped = 0
    task_ids = []

    for cid in body.content_ids:
        entry = snapshot_map.get(cid)
        if not entry:
            skipped += 1
            continue
        snapshot, platform = entry
        if snapshot.content_type != "video":
            skipped += 1
            continue
        if cid in existing_ids:
            skipped += 1
            continue

        task = DownloadTask(
            content_snapshot_id=cid,
            platform=platform or "unknown",
            content_id=snapshot.content_id,
            title=snapshot.title,
            status=DownloadStatus.PENDING.value,
        )
        db.add(task)
        task_ids.append(task)  # 暂存引用，commit 后才有 id
        created += 1

    await db.commit()

    # 入队（此时 task 已有 id）
    for task in task_ids:
        await db.refresh(task)
        await download_engine.enqueue(task.id)

    logger.info(f"Batch download: created={created}, skipped={skipped}")
    return {
        "message": f"已创建 {created} 个下载任务",
        "created": created,
        "skipped": skipped,
    }


# ---- 任务列表 ----

@router.get("", response_model=DownloadListResponse)
async def list_downloads(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """列出下载任务。"""
    stmt = select(DownloadTask).order_by(DownloadTask.id.desc())
    if status:
        stmt = stmt.where(DownloadTask.status == status)
    if platform:
        stmt = stmt.where(DownloadTask.platform == platform)

    # 总数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    # 获取关联 URL
    snapshot_ids = [t.content_snapshot_id for t in tasks]
    urls_map = {}
    if snapshot_ids:
        url_result = await db.execute(
            select(ContentSnapshot.id, ContentSnapshot.url).where(
                ContentSnapshot.id.in_(snapshot_ids)
            )
        )
        for sid, url in url_result:
            urls_map[sid] = url

    items = [_task_to_response(t, urls_map.get(t.content_snapshot_id, "")) for t in tasks]
    return DownloadListResponse(total=total, items=items)


# ---- 任务详情 ----

@router.get("/stats", response_model=DownloadStatsResponse)
async def download_stats(db: AsyncSession = Depends(get_db)):
    """下载统计。"""
    total = (await db.execute(
        select(func.count(DownloadTask.id))
    )).scalar() or 0

    pending = (await db.execute(
        select(func.count(DownloadTask.id)).where(
            DownloadTask.status == DownloadStatus.PENDING.value
        )
    )).scalar() or 0

    downloading = (await db.execute(
        select(func.count(DownloadTask.id)).where(
            DownloadTask.status == DownloadStatus.DOWNLOADING.value
        )
    )).scalar() or 0

    completed = (await db.execute(
        select(func.count(DownloadTask.id)).where(
            DownloadTask.status == DownloadStatus.COMPLETED.value
        )
    )).scalar() or 0

    failed = (await db.execute(
        select(func.count(DownloadTask.id)).where(
            DownloadTask.status == DownloadStatus.FAILED.value
        )
    )).scalar() or 0

    total_size = (await db.execute(
        select(func.coalesce(func.sum(DownloadTask.file_size), 0)).where(
            DownloadTask.status == DownloadStatus.COMPLETED.value
        )
    )).scalar() or 0

    return DownloadStatsResponse(
        total=total,
        pending=pending,
        downloading=downloading,
        completed=completed,
        failed=failed,
        total_size=total_size,
    )


@router.get("/{task_id}", response_model=DownloadTaskResponse)
async def get_download(task_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个下载任务详情。"""
    task = await db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, f"下载任务不存在: id={task_id}")

    url = ""
    if task.content_snapshot_id:
        snapshot = await db.get(ContentSnapshot, task.content_snapshot_id)
        if snapshot:
            url = snapshot.url

    return _task_to_response(task, url)


# ---- 播放 / 另存为 ----

@router.get("/{task_id}/file")
async def download_task_file(
    task_id: int,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """提供已下载视频的播放/下载。

    - 默认 inline（``Content-Disposition: inline``）：供前端 ``<video>`` 在线预览。
    - ``?download=true``：attachment，触发浏览器"另存为"，文件名沿用下载时的标题。
    """
    task = await db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, f"下载任务不存在: id={task_id}")
    if task.status != DownloadStatus.COMPLETED.value:
        raise HTTPException(400, "任务尚未完成，暂无文件可播放")
    if not task.file_path or not os.path.exists(task.file_path):
        raise HTTPException(404, "本地文件不存在（可能已被删除）")

    # 防御：确认文件确实落在 downloads_dir 下，避免被构造路径越界读取
    real_path = Path(task.file_path).resolve()
    if not real_path.is_relative_to(settings.downloads_dir.resolve()):
        raise HTTPException(403, "非法文件路径")

    return FileResponse(
        str(real_path),
        media_type="video/mp4",
        filename=real_path.name,
        content_disposition_type="attachment" if download else "inline",
    )


# ---- 重试失败任务 ----

@router.post("/{task_id}/retry", status_code=202)
async def retry_download(task_id: int, db: AsyncSession = Depends(get_db)):
    """重试失败的下载任务。"""
    task = await db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, f"下载任务不存在: id={task_id}")
    if task.status != DownloadStatus.FAILED.value:
        raise HTTPException(400, "只能重试失败的任务")

    task.status = DownloadStatus.PENDING.value
    task.error_message = ""
    task.retry_count = 0
    await db.commit()
    await download_engine.enqueue(task.id)

    return {"message": f"已重新入队: task_id={task_id}"}


# ---- 删除任务 ----

@router.delete("/{task_id}")
async def delete_download(task_id: int, db: AsyncSession = Depends(get_db)):
    """删除下载任务，同时删除本地文件。"""
    task = await db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, f"下载任务不存在: id={task_id}")

    # 删除本地文件
    if task.file_path and os.path.exists(task.file_path):
        try:
            os.unlink(task.file_path)
            logger.info(f"Deleted file: {task.file_path}")
        except Exception as e:
            logger.warning(f"Failed to delete file {task.file_path}: {e}")

    await db.delete(task)
    await db.commit()
    return {"message": f"已删除下载任务: task_id={task_id}"}


# ---- 批量删除 ----

@router.delete("/batch")
async def batch_delete_downloads(
    body: DownloadBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量删除下载任务。"""
    result = await db.execute(
        select(DownloadTask).where(DownloadTask.id.in_(body.task_ids))
    )
    tasks = result.scalars().all()

    deleted = 0
    for task in tasks:
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.unlink(task.file_path)
            except Exception:
                pass
        await db.delete(task)
        deleted += 1

    await db.commit()
    return {"message": f"已删除 {deleted} 个下载任务"}


# ---- 清理已完成/失败 ----

@router.delete("/cleanup")
async def cleanup_downloads(
    status: str = "completed",
    db: AsyncSession = Depends(get_db),
):
    """清理已完成或失败的任务。可选清理已完成(completed)/失败(failed)/全部(all)。"""
    if status == "all":
        statuses = [
            DownloadStatus.COMPLETED.value,
            DownloadStatus.FAILED.value,
        ]
    else:
        statuses = [status]

    result = await db.execute(
        select(DownloadTask).where(DownloadTask.status.in_(statuses))
    )
    tasks = result.scalars().all()

    cleaned = 0
    for task in tasks:
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.unlink(task.file_path)
            except Exception:
                pass
        await db.delete(task)
        cleaned += 1

    await db.commit()
    return {"message": f"已清理 {cleaned} 个任务 (status={status})"}
