"""视频分析任务 REST API。"""
import logging
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import (
    VideoAnalysis, AnalysisStatus, DownloadTask, DownloadStatus, ContentSnapshot,
)
from backend.schemas import (
    VideoAnalysisResponse, AnalysisListResponse, AnalysisCreateRequest,
    AnalysisStatsResponse, AnalysisCheckResponse,
)
from backend.engine.analyzer import analysis_engine
from backend.engine.asr import get_asr_provider

logger = logging.getLogger("fapai.api.analysis")
router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


def _to_response(a: VideoAnalysis) -> VideoAnalysisResponse:
    """将 ORM 对象转换为响应 schema。"""
    return VideoAnalysisResponse(
        id=a.id,
        download_task_id=a.download_task_id,
        content_snapshot_id=a.content_snapshot_id or 0,
        platform=a.platform or "",
        content_id=a.content_id or "",
        title=a.title or "",
        status=a.status,
        stage=a.stage or "",
        progress=a.progress,
        asr_provider=a.asr_provider or "",
        asr_segments=a.asr_segments or [],
        transcript=a.transcript or "",
        duration_sec=a.duration_sec or 0,
        language=a.language or "",
        script_analysis=a.script_analysis or "",
        content_summary=a.content_summary or "",
        llm_model=a.llm_model or "",
        error_message=a.error_message or "",
        retry_count=a.retry_count or 0,
        created_at=a.created_at,
        started_at=a.started_at,
        finished_at=a.finished_at,
    )


# ---- 就绪检测（ffmpeg / ASR / LLM）----
# 注意：/check、/stats 等静态路径必须注册在 /{analysis_id} 之前，避免被路径参数吞掉。

@router.get("/check", response_model=AnalysisCheckResponse)
async def check_analysis():
    """检测 ffmpeg / ASR / LLM 是否就绪。"""
    ffmpeg_path = shutil.which("ffmpeg") or ""
    ffmpeg_available = bool(ffmpeg_path)

    asr_ready = False
    asr_message = ""
    try:
        get_asr_provider()
        asr_ready = True
        asr_message = f"provider={settings.asr_provider}"
    except Exception as e:
        asr_message = str(e)

    llm_ready = bool(settings.llm_base_url and settings.llm_api_key)
    llm_message = "ok" if llm_ready else "未配置 FAPAI_LLM_BASE_URL / FAPAI_LLM_API_KEY"

    return AnalysisCheckResponse(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=ffmpeg_path,
        asr_provider=settings.asr_provider,
        asr_ready=asr_ready,
        asr_message=asr_message,
        llm_ready=llm_ready,
        llm_message=llm_message,
    )


# ---- 创建分析任务（按下载任务）----

@router.post("", response_model=VideoAnalysisResponse, status_code=202)
async def create_analysis(body: AnalysisCreateRequest, db: AsyncSession = Depends(get_db)):
    """基于已完成的下载任务创建/复用分析任务并入队。"""
    dtask = await db.get(DownloadTask, body.download_task_id)
    if not dtask:
        raise HTTPException(404, f"下载任务不存在: id={body.download_task_id}")
    if dtask.status != DownloadStatus.COMPLETED.value:
        raise HTTPException(400, "仅可分析已完成的下载")
    if not dtask.file_path:
        raise HTTPException(400, "该下载没有本地文件")

    existing = await db.execute(
        select(VideoAnalysis).where(VideoAnalysis.download_task_id == body.download_task_id)
    )
    a = existing.scalar_one_or_none()
    if a is None:
        a = VideoAnalysis(
            download_task_id=dtask.id,
            content_snapshot_id=dtask.content_snapshot_id or 0,
            platform=dtask.platform or "",
            content_id=dtask.content_id or "",
            title=dtask.title or "",
            status=AnalysisStatus.PENDING.value,
        )
        db.add(a)
    else:
        # 复用已存在的记录：清空旧结果后重置为 PENDING
        a.status = AnalysisStatus.PENDING.value
        a.error_message = ""
        a.retry_count = 0
        a.script_analysis = ""
        a.content_summary = ""
    await db.commit()
    await db.refresh(a)
    await analysis_engine.enqueue(a.id)
    return _to_response(a)


# ---- 任务列表 ----

@router.get("", response_model=AnalysisListResponse)
async def list_analysis(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """列出分析任务，支持按状态/平台筛选与分页。"""
    stmt = select(VideoAnalysis).order_by(VideoAnalysis.id.desc())
    if status:
        stmt = stmt.where(VideoAnalysis.status == status)
    if platform:
        stmt = stmt.where(VideoAnalysis.platform == platform)

    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar() or 0

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    tasks = (await db.execute(stmt)).scalars().all()
    return AnalysisListResponse(total=total, items=[_to_response(t) for t in tasks])


# ---- 统计 ----

@router.get("/stats", response_model=AnalysisStatsResponse)
async def analysis_stats(db: AsyncSession = Depends(get_db)):
    """分析任务状态统计。"""
    async def count(status_value: str) -> int:
        return (await db.execute(
            select(func.count(VideoAnalysis.id)).where(
                VideoAnalysis.status == status_value
            )
        )).scalar() or 0

    return AnalysisStatsResponse(
        total=(await db.execute(select(func.count(VideoAnalysis.id)))).scalar() or 0,
        pending=await count(AnalysisStatus.PENDING.value),
        analyzing=(await count(AnalysisStatus.EXTRACTING.value)
                   + await count(AnalysisStatus.TRANSCRIBING.value)
                   + await count(AnalysisStatus.ANALYZING.value)),
        completed=await count(AnalysisStatus.COMPLETED.value),
        failed=await count(AnalysisStatus.FAILED.value),
    )


# ---- 任务详情 ----

@router.get("/{analysis_id}", response_model=VideoAnalysisResponse)
async def get_analysis(analysis_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个分析任务详情。"""
    a = await db.get(VideoAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, f"分析任务不存在: id={analysis_id}")
    return _to_response(a)


# ---- 重试 ----

@router.post("/{analysis_id}/retry", status_code=202)
async def retry_analysis(
    analysis_id: int, force_asr: bool = False, db: AsyncSession = Depends(get_db)
):
    """重置任务状态并重新入队。force_asr=True 时清空已有 ASR 结果强制重新转写。"""
    a = await db.get(VideoAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, f"分析任务不存在: id={analysis_id}")
    a.status = AnalysisStatus.PENDING.value
    a.error_message = ""
    a.retry_count = 0
    if force_asr:
        a.asr_segments = []
        a.transcript = ""
    await db.commit()
    await analysis_engine.enqueue(a.id)
    return {"message": f"已重新入队: analysis_id={analysis_id}", "force_asr": force_asr}


# ---- 删除 ----

@router.delete("/{analysis_id}")
async def delete_analysis(analysis_id: int, db: AsyncSession = Depends(get_db)):
    """删除分析记录（不删除已下载的视频文件）。"""
    a = await db.get(VideoAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, f"分析任务不存在: id={analysis_id}")
    await db.delete(a)
    await db.commit()
    return {"message": f"已删除分析记录: analysis_id={analysis_id}"}
