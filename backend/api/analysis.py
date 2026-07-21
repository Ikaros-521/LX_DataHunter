"""视频分析任务 REST API。"""
import json
import logging
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import (
    VideoAnalysis, AnalysisStatus, DownloadTask, DownloadStatus,
    ALL_ANALYSIS_TASKS,
)
from backend.schemas import (
    VideoAnalysisResponse, AnalysisListResponse, AnalysisCreateRequest,
    AnalysisStatsResponse, AnalysisCheckResponse,
    AnalysisCandidateResponse, AnalysisCandidateListResponse,
    AnalysisBatchCreateRequest,
    AnalysisDigestRequest, AnalysisDigestResponse,
    AnalysisChatRequest, AnalysisChatResponse,
    AnalysisBatchDetailRequest,
)
from backend.engine.analyzer import analysis_engine
from backend.engine.asr import get_asr_provider
from backend.engine.llm_client import LlmClient, build_digest_prompt

logger = logging.getLogger("lx_datahunter.api.analysis")
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
        knowledge_summary=a.knowledge_summary or "",
        enabled_tasks=list(a.enabled_tasks or ALL_ANALYSIS_TASKS),
        llm_model=a.llm_model or "",
        error_message=a.error_message or "",
        retry_count=a.retry_count or 0,
        created_at=a.created_at,
        started_at=a.started_at,
        finished_at=a.finished_at,
    )


def _build_analysis_from_task(dtask: DownloadTask, enabled_tasks: Optional[list[str]] = None) -> VideoAnalysis:
    """根据下载任务新建一个 PENDING 分析记录（未 commit）。

    enabled_tasks 为 None 时默认启用全部 LLM 分析项。
    """
    return VideoAnalysis(
        download_task_id=dtask.id,
        content_snapshot_id=dtask.content_snapshot_id or 0,
        platform=dtask.platform or "",
        content_id=dtask.content_id or "",
        title=dtask.title or "",
        status=AnalysisStatus.PENDING.value,
        enabled_tasks=list(enabled_tasks) if enabled_tasks else list(ALL_ANALYSIS_TASKS),
    )


def _reset_analysis(a: VideoAnalysis) -> None:
    """复用已存在的分析记录：清空旧结果后重置为 PENDING（未 commit）。"""
    a.status = AnalysisStatus.PENDING.value
    a.error_message = ""
    a.retry_count = 0
    a.script_analysis = ""
    a.content_summary = ""
    a.knowledge_summary = ""


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
    llm_message = "ok" if llm_ready else "未配置 LLM_BASE_URL / LLM_API_KEY"

    return AnalysisCheckResponse(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=ffmpeg_path,
        asr_provider=settings.asr_provider,
        asr_ready=asr_ready,
        asr_message=asr_message,
        llm_ready=llm_ready,
        llm_message=llm_message,
    )


# ---- 可分析视频候选列表 ----
# 注意：必须在 /{analysis_id} 之前注册，否则会被路径参数吞掉。

# 分析中聚合态对应的底层状态
_ANALYZING_STATUSES = [
    AnalysisStatus.PENDING.value,
    AnalysisStatus.EXTRACTING.value,
    AnalysisStatus.TRANSCRIBING.value,
    AnalysisStatus.ANALYZING.value,
]


@router.get("/candidates", response_model=AnalysisCandidateListResponse)
async def list_analysis_candidates(
    search: Optional[str] = None,
    platform: Optional[str] = None,
    analysis_status: str = "none",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """列出"可分析视频"候选，支持按标题搜索、平台/分析状态筛选与分页。

    数据源：已下载完成且有本地文件的 DownloadTask，LEFT JOIN VideoAnalysis。
    每条返回 analysis_id / analysis_status / analysis_progress，便于前端
    直接判断"这个视频分析过没有"。

    - ``search``：标题 LIKE 模糊匹配。
    - ``analysis_status``：none(默认,仅未分析) | pending | analyzing |
      completed | failed | all。
    """
    # 构造带 LEFT JOIN 的基础查询
    base = (
        select(
            DownloadTask.id,
            DownloadTask.content_snapshot_id,
            DownloadTask.platform,
            DownloadTask.content_id,
            DownloadTask.title,
            DownloadTask.file_size,
            DownloadTask.created_at,
            VideoAnalysis.id.label("va_id"),
            VideoAnalysis.status.label("va_status"),
            VideoAnalysis.progress.label("va_progress"),
        )
        .outerjoin(VideoAnalysis, VideoAnalysis.download_task_id == DownloadTask.id)
        .where(
            DownloadTask.status == DownloadStatus.COMPLETED.value,
            DownloadTask.file_path.isnot(None),
            DownloadTask.file_path != "",
        )
    )

    if search:
        base = base.where(DownloadTask.title.like(f"%{search}%"))
    if platform:
        base = base.where(DownloadTask.platform == platform)

    status_filter = (analysis_status or "none").lower()
    if status_filter == "none":
        # 仅未分析：JOIN 右侧为空
        base = base.where(VideoAnalysis.id.is_(None))
    elif status_filter == "analyzing":
        base = base.where(VideoAnalysis.status.in_(_ANALYZING_STATUSES))
    elif status_filter in {
        AnalysisStatus.PENDING.value,
        AnalysisStatus.COMPLETED.value,
        AnalysisStatus.FAILED.value,
    }:
        base = base.where(VideoAnalysis.status == status_filter)
    elif status_filter == "extracting":
        base = base.where(VideoAnalysis.status == AnalysisStatus.EXTRACTING.value)
    elif status_filter == "transcribing":
        base = base.where(VideoAnalysis.status == AnalysisStatus.TRANSCRIBING.value)
    # status_filter == "all"：不加分析状态条件

    base = base.order_by(DownloadTask.id.desc())

    # 总数
    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    # 分页
    rows = (await db.execute(
        base.offset((page - 1) * page_size).limit(page_size)
    )).all()

    def _agg_status(s: Optional[str]) -> str:
        """把底层分析状态聚合到候选展示态。"""
        if s is None:
            return "none"
        if s in _ANALYZING_STATUSES:
            return "analyzing"
        return s  # completed / failed 原样返回

    items = [
        AnalysisCandidateResponse(
            download_task_id=r.id,
            content_snapshot_id=r.content_snapshot_id or 0,
            platform=r.platform or "",
            content_id=r.content_id or "",
            title=r.title or "",
            file_size=r.file_size or 0,
            created_at=r.created_at,
            analysis_id=r.va_id,
            analysis_status=_agg_status(r.va_status),
            analysis_progress=r.va_progress or 0,
        )
        for r in rows
    ]
    return AnalysisCandidateListResponse(total=total, items=items)


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
        a = _build_analysis_from_task(dtask, body.enabled_tasks)
        db.add(a)
    else:
        _reset_analysis(a)
        # 复用时同步更新启用的分析项（可能用户重新勾选了不同的项）
        if body.enabled_tasks is not None:
            a.enabled_tasks = list(body.enabled_tasks)
    await db.commit()
    await db.refresh(a)
    await analysis_engine.enqueue(a.id)
    return _to_response(a)


# ---- 批量创建分析任务（按下载任务）----

@router.post("/batch", status_code=202)
async def batch_create_analysis(body: AnalysisBatchCreateRequest, db: AsyncSession = Depends(get_db)):
    """批量创建分析任务并统一入队。

    单次事务内逐个处理：校验下载已完成且有本地文件、已存在则复用并重置为
    PENDING、否则新建。事务提交后按 id 顺序逐个入队，由 ``analysis_engine``
    按并发限制自动排队执行。
    """
    # 去重，保持入参顺序
    ids = list(dict.fromkeys(body.download_task_ids))

    # 一次性取所有相关下载任务
    tasks_result = await db.execute(
        select(DownloadTask).where(DownloadTask.id.in_(ids))
    )
    task_map: dict[int, DownloadTask] = {t.id: t for t in tasks_result.scalars().all()}

    # 一次性取已存在的分析记录
    existing_result = await db.execute(
        select(VideoAnalysis).where(VideoAnalysis.download_task_id.in_(ids))
    )
    existing_map: dict[int, VideoAnalysis] = {
        a.download_task_id: a for a in existing_result.scalars().all()
    }

    created = 0       # 新建的分析数
    reused = 0        # 复用并重置的分析数
    skipped = 0       # 跳过（下载不存在/未完成/无文件）
    to_enqueue: list[VideoAnalysis] = []

    for tid in ids:
        dtask = task_map.get(tid)
        if not dtask:
            skipped += 1
            continue
        if dtask.status != DownloadStatus.COMPLETED.value or not dtask.file_path:
            skipped += 1
            continue
        a = existing_map.get(tid)
        if a is None:
            a = _build_analysis_from_task(dtask, body.enabled_tasks)
            db.add(a)
            created += 1
        else:
            _reset_analysis(a)
            if body.enabled_tasks is not None:
                a.enabled_tasks = list(body.enabled_tasks)
            reused += 1
        to_enqueue.append(a)

    if not to_enqueue:
        # 没有可处理的任务时给出明确提示，而不是默默返回 0
        raise HTTPException(400, "没有可分析的视频（未找到已完成且有本地文件的下载）")

    await db.commit()
    # 统一入队（refresh 拿到最终 id，包括新建对象）
    enqueued = 0
    for a in to_enqueue:
        await db.refresh(a)
        await analysis_engine.enqueue(a.id)
        enqueued += 1

    logger.info(
        f"Batch analysis: created={created}, reused={reused}, skipped={skipped}, enqueued={enqueued}"
    )
    return {
        "message": f"已入队 {enqueued} 个分析任务（新建 {created}，复用 {reused}，跳过 {skipped}）",
        "created": created,
        "reused": reused,
        "skipped": skipped,
        "enqueued": enqueued,
    }


# ---- 跨视频"大总结"（不落库，同步生成即返回）----
# 注意：必须在 /{analysis_id} 之前注册，否则会被路径参数吞掉。

# task 参数到 VideoAnalysis 内容字段的映射
_DIGEST_FIELD = {
    "summary": "content_summary",
    "knowledge": "knowledge_summary",
}


@router.post("/digest", response_model=AnalysisDigestResponse)
async def create_digest(body: AnalysisDigestRequest, db: AsyncSession = Depends(get_db)):
    """把多个已完成分析的"单项总结"聚合成一份跨视频大总结。

    - 不落库：结果直接返回，刷新即丢。
    - 同步调用 LLM（单次 chat），通常 10-30s，由前端 loading 态承接。
    - 输入用各视频已有的 ``content_summary``（task=summary）或
      ``knowledge_summary``（task=knowledge），不读原始逐字稿。
    """
    if body.task not in _DIGEST_FIELD:
        raise HTTPException(400, f"不支持的 task: {body.task}（可选 summary / knowledge）")

    # 去重保序
    ids = list(dict.fromkeys(body.analysis_ids))

    result = await db.execute(
        select(VideoAnalysis).where(
            VideoAnalysis.id.in_(ids),
            VideoAnalysis.status == AnalysisStatus.COMPLETED.value,
        )
    )
    analyses = {a.id: a for a in result.scalars().all()}

    field_name = _DIGEST_FIELD[body.task]
    items: list[tuple[str, str]] = []
    for aid in ids:
        a = analyses.get(aid)
        if not a:
            continue
        text = getattr(a, field_name) or ""
        text = text.strip()
        if not text:
            continue
        items.append((a.title or "", text))

    if not items:
        raise HTTPException(400, "没有可聚合的内容（选中分析均未生成该维度的总结）")

    client = LlmClient.from_settings()
    system, user = build_digest_prompt(items)
    digest = await client.chat(system, user)

    return AnalysisDigestResponse(
        digest=digest,
        source_count=len(items),
        llm_model=settings.llm_model,
    )


# ---- 对总结内容多轮对话（不落库）----
# 注意：必须在 /{analysis_id} 之前注册，否则会被路径参数吞掉。

async def _build_chat_context(
    db: AsyncSession, analysis_ids: list[int], task: str
) -> str:
    """读取选中分析的指定维度总结，拼成对话 system 上下文文本。

    抛 HTTPException(400) 当 task 非法或无可聚合内容。供 /chat 与
    /chat/stream 共享。
    """
    if task not in _DIGEST_FIELD:
        raise HTTPException(400, f"不支持的 task: {task}（可选 summary / knowledge）")

    ids = list(dict.fromkeys(analysis_ids))
    result = await db.execute(
        select(VideoAnalysis).where(
            VideoAnalysis.id.in_(ids),
            VideoAnalysis.status == AnalysisStatus.COMPLETED.value,
        )
    )
    analyses = {a.id: a for a in result.scalars().all()}

    field_name = _DIGEST_FIELD[task]
    parts = []
    for aid in ids:
        a = analyses.get(aid)
        if not a:
            continue
        text = (getattr(a, field_name) or "").strip()
        if not text:
            continue
        parts.append(f"### {a.title or '(无标题)'}\n{text}")

    if not parts:
        raise HTTPException(400, "没有可作为对话上下文的总结内容")

    context = "\n\n".join(parts)
    return (
        "你是知识助理。以下是一组视频的总结内容，作为你回答问题的唯一依据。"
        "请基于这些素材回答用户问题；如果素材中没有相关信息，请明确说明"
        "「素材中未提及」，不要编造。\n\n"
        f"=== 视频总结素材 ===\n{context}"
    )


@router.post("/chat", response_model=AnalysisChatResponse)
async def chat_with_analyses(body: AnalysisChatRequest, db: AsyncSession = Depends(get_db)):
    """对选中视频的总结内容进行多轮对话（非流式）。

    - 把选中分析的单项总结（content_summary 或 knowledge_summary）拼进 system，
      作为问答的固定上下文。
    - 请求体的 messages 保留历史问答，支持追问与上下文连贯。
    - 不落库：每次请求无状态，上下文完全由前端维护。
    """
    system = await _build_chat_context(db, body.analysis_ids, body.task)
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": m.role, "content": m.content} for m in body.messages)

    client = LlmClient.from_settings()
    answer = await client.chat_messages(messages)

    return AnalysisChatResponse(answer=answer, llm_model=settings.llm_model)


@router.post("/chat/stream")
async def chat_with_analyses_stream(body: AnalysisChatRequest, db: AsyncSession = Depends(get_db)):
    """对选中视频的总结内容进行多轮对话（SSE 流式打字机效果）。

    事件格式：
    - ``data: {"token": "..."}\\n\\n`` 逐 token 输出
    - ``data: {"done": true, "llm_model": "..."}\\n\\n`` 结束
    - ``data: {"error": "..."}\\n\\n`` 出错
    """
    # 上下文构造必须在生成器外完成，以便在此处抛 HTTPException
    system = await _build_chat_context(db, body.analysis_ids, body.task)
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": m.role, "content": m.content} for m in body.messages)

    async def event_generator():
        client = LlmClient.from_settings()
        try:
            async for token in client.chat_messages_stream(messages):
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True, 'llm_model': settings.llm_model}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"chat stream error: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': f'{type(e).__name__}: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- 批量取分析详情（对比页跨页取数用）----
# 注意：必须在 /{analysis_id} 之前注册。

@router.post("/batch-detail", response_model=AnalysisListResponse)
async def batch_detail(body: AnalysisBatchDetailRequest, db: AsyncSession = Depends(get_db)):
    """按 id 列表批量取分析详情，不依赖列表分页（对比页跨页选中用）。"""
    ids = list(dict.fromkeys(body.analysis_ids))
    result = await db.execute(
        select(VideoAnalysis).where(VideoAnalysis.id.in_(ids))
    )
    items = {a.id: a for a in result.scalars().all()}
    # 按入参顺序返回
    ordered = [items[i] for i in ids if i in items]
    return AnalysisListResponse(total=len(ordered), items=[_to_response(a) for a in ordered])


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
