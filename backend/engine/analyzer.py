"""视频分析引擎 — 照抄 download_engine 的异步队列/状态机/进度模式。

管线：抽音频(ffmpeg) → ASR → 两类 LLM 分析。结果落 VideoAnalysis。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any, Callable, Coroutine

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models import (
    VideoAnalysis, AnalysisStatus, DownloadTask, DownloadStatus, beijing_now,
    ALL_ANALYSIS_TASKS,
)
from backend.engine.asr import (
    get_asr_provider, segments_to_transcript, segments_to_dicts, AsrProvider,
)
from backend.engine.llm_client import (
    LlmClient, build_script_prompt, build_summary_prompt, build_knowledge_prompt,
)

logger = logging.getLogger("lx_datahunter.analyzer")

ProgressBroadcast = Callable[[dict], Coroutine[Any, Any, None]]

# 各阶段在整个 0-100 进度条上的区间：extracting 0-10，transcribing 10-60，analyzing 60-99
_STAGE_RANGES = {
    "extracting": (0.0, 10.0),
    "transcribing": (10.0, 60.0),
    "analyzing": (60.0, 99.0),
}


def stage_progress(stage: str, ratio: float) -> float:
    """把某阶段内 0-1 的进度 ratio 映射到整体 0-100。"""
    lo, hi = _STAGE_RANGES.get(stage, (0.0, 100.0))
    ratio = max(0.0, min(1.0, ratio))
    return round(lo + (hi - lo) * ratio, 1)


class AnalysisEngine:
    """视频分析队列管理器。

    用 asyncio.Queue + 多 worker + Semaphore 限制并发，照搬 download_engine
    的生命周期/重试/中断恢复/广播模式。
    """

    def __init__(self):
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._semaphore: asyncio.Semaphore | None = None
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._broadcast: ProgressBroadcast | None = None

    def set_broadcast(self, fn: ProgressBroadcast):
        self._broadcast = fn

    # ---- 生命周期 ----

    async def start(self):
        """启动分析引擎（在 FastAPI lifespan 中调用）。"""
        # 恢复中断任务
        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(VideoAnalysis)
                .where(VideoAnalysis.status.in_([
                    AnalysisStatus.EXTRACTING.value,
                    AnalysisStatus.TRANSCRIBING.value,
                    AnalysisStatus.ANALYZING.value,
                ]))
                .values(
                    status=AnalysisStatus.PENDING.value,
                    error_message="Server restarted — analysis interrupted",
                )
            )
            await db.commit()
            logger.info("Reset interrupted analyses to pending")

        # 重新入队 pending 任务
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(VideoAnalysis.id).where(
                    VideoAnalysis.status == AnalysisStatus.PENDING.value
                )
            )
            pending_ids = result.scalars().all()
            for aid in pending_ids:
                self._queue.put_nowait(aid)
            if pending_ids:
                logger.info(f"Re-queued {len(pending_ids)} pending analyses")

        self._semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
        self._running = True
        worker_count = settings.max_concurrent_analyses
        for i in range(worker_count):
            self._workers.append(asyncio.create_task(self._worker(i)))
        loop_type = type(asyncio.get_running_loop()).__name__
        logger.info(f"Analysis engine started: {worker_count} workers on {loop_type}")
        if loop_type == "SelectorEventLoop":
            logger.warning(
                "当前事件循环是 SelectorEventLoop（通常因 --reload）。分析引擎已改用 "
                "subprocess.run（不依赖 asyncio 子进程），不受影响；但下载引擎的 yt-dlp "
                "等异步子进程功能仍会失败，如需用到建议用 python run.py 启动。"
            )

    async def stop(self):
        """停止分析引擎。"""
        self._running = False
        for _ in self._workers:
            try:
                self._queue.put_nowait(-1)
            except asyncio.QueueFull:
                pass
        for w in self._workers:
            try:
                await asyncio.wait_for(w, timeout=5)
            except (asyncio.TimeoutError, Exception):
                w.cancel()
        self._workers.clear()
        logger.info("Analysis engine stopped")

    async def enqueue(self, analysis_id: int):
        """将分析任务加入队列。"""
        await self._queue.put(analysis_id)

    # ---- worker ----

    async def _worker(self, worker_id: int):
        logger.info(f"Analysis worker-{worker_id} started")
        while self._running:
            try:
                aid = await asyncio.wait_for(self._queue.get(), timeout=2)
            except asyncio.TimeoutError:
                continue
            if aid == -1:
                self._queue.task_done()
                break
            try:
                async with self._semaphore:
                    await self._process_task(aid)
            except Exception as e:
                logger.error(f"Worker-{worker_id} unhandled error for analysis {aid}: {e}")
            finally:
                self._queue.task_done()
        logger.info(f"Analysis worker-{worker_id} stopped")

    # ---- 单任务管线 ----

    async def _process_task(self, analysis_id: int, force_asr: bool = False):
        """处理单个分析任务。"""
        async with AsyncSessionLocal() as db:
            a = await db.get(VideoAnalysis, analysis_id)
            if not a:
                logger.warning(f"Analysis {analysis_id} not found")
                return
            if a.status not in (
                AnalysisStatus.PENDING.value,
                AnalysisStatus.FAILED.value,
            ):
                return

            dtask = await db.get(DownloadTask, a.download_task_id)
            if (
                not dtask
                or dtask.status != DownloadStatus.COMPLETED.value
                or not dtask.file_path
                or not os.path.exists(dtask.file_path)
            ):
                await self._fail(db, a, "关联的下载不存在或未完成")
                return

            a.status = AnalysisStatus.EXTRACTING.value
            a.stage = "抽取音频"
            a.started_at = beijing_now()
            a.error_message = ""
            await db.commit()
            await self._broadcast_progress(a)

            audio_path: str | None = None
            try:
                # 1. 抽音频
                audio_path = await self._extract_audio(dtask.file_path)
                await self._set_progress(db, a, stage_progress("extracting", 1.0))

                # 2. ASR（缓存命中且非强制则跳过）
                provider = get_asr_provider()
                if force_asr or not a.asr_segments:
                    a.status = AnalysisStatus.TRANSCRIBING.value
                    a.stage = "语音识别"
                    a.asr_provider = provider.name
                    await db.commit()
                    await self._broadcast_progress(a)
                    result = await provider.transcribe(audio_path)
                    a.asr_segments = segments_to_dicts(result.segments)
                    a.transcript = segments_to_transcript(result.segments)
                    a.language = result.language
                    a.duration_sec = result.duration_sec or a.duration_sec
                    await db.commit()
                    await self._broadcast_progress(a)
                await self._set_progress(db, a, stage_progress("transcribing", 1.0))

                if not a.transcript.strip():
                    await self._fail(db, a, "语音识别结果为空（可能是无语音视频）")
                    return

                # 3. LLM 分析（按 enabled_tasks 动态并发）
                a.status = AnalysisStatus.ANALYZING.value
                a.stage = "大模型分析"
                await db.commit()
                await self._broadcast_progress(a)

                tasks_enabled = a.enabled_tasks or ALL_ANALYSIS_TASKS
                client = LlmClient.from_settings()
                # 构造 [(key, awaitable)]，只跑勾选的 LLM 分析项
                llm_jobs: list[tuple[str, Any]] = []
                if "summary" in tasks_enabled:
                    s_sys, s_user = build_summary_prompt(a.transcript)
                    llm_jobs.append(("summary", client.chat(s_sys, s_user)))
                if "script" in tasks_enabled:
                    p_sys, p_user = build_script_prompt(a.transcript)
                    llm_jobs.append(("script", client.chat(p_sys, p_user)))
                if "knowledge" in tasks_enabled:
                    k_sys, k_user = build_knowledge_prompt(a.transcript)
                    llm_jobs.append(("knowledge", client.chat(k_sys, k_user)))

                if not llm_jobs:
                    await self._fail(db, a, "未勾选任何 LLM 分析项（enabled_tasks 为空）")
                    return

                results = await asyncio.gather(*[job for _, job in llm_jobs])
                for (key, _), text in zip(llm_jobs, results):
                    if key == "summary":
                        a.content_summary = text
                    elif key == "script":
                        a.script_analysis = text
                    elif key == "knowledge":
                        a.knowledge_summary = text
                a.llm_model = settings.llm_model

                a.status = AnalysisStatus.COMPLETED.value
                a.stage = "完成"
                a.progress = 100
                a.finished_at = beijing_now()
                await db.commit()
                await self._broadcast_progress(a)
                logger.info(f"Analysis completed: {analysis_id}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 用 type(e).__name__ + exc_info，避免 NotImplementedError 等空消息异常被吞掉
                msg = f"{type(e).__name__}: {e}"[:500]
                logger.error(f"Analysis failed: {analysis_id}, error={msg}", exc_info=True)
                a.retry_count += 1
                if a.retry_count >= settings.max_analysis_retries:
                    await self._fail(db, a, msg)
                else:
                    a.status = AnalysisStatus.PENDING.value
                    a.error_message = f"尝试 {a.retry_count}/{settings.max_analysis_retries} 失败: {msg}"
                    await db.commit()
                    await self._broadcast_progress(a)
                    self._queue.put_nowait(a.id)
            finally:
                if audio_path:
                    try:
                        os.unlink(audio_path)
                    except Exception:
                        pass

    # ---- 辅助 ----

    async def _extract_audio(self, video_path: str) -> str:
        """用 ffmpeg 抽出 16k 单声道 wav。

        用标准库 subprocess.run（阻塞）跑在执行器线程里——不依赖 asyncio 子进程
        支持，因此在 SelectorEventLoop（Windows --reload）下也能工作。
        """
        fd, out = tempfile.mkstemp(prefix="lx_datahunter_audio_", suffix=".wav")
        os.close(fd)  # ffmpeg 会自己写这个文件
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, AnalysisEngine._ffmpeg_extract_sync, video_path, out
            )
        except Exception:
            try:
                os.unlink(out)
            except Exception:
                pass
            raise
        return out

    @staticmethod
    def _ffmpeg_extract_sync(video_path: str, out_path: str) -> None:
        """阻塞调用 ffmpeg（在执行器线程里跑）。用 subprocess.run，不依赖事件循环。"""
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", out_path],
            capture_output=True,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", "replace")[-500:]
            raise RuntimeError(f"ffmpeg 抽音频失败: {err}")

    async def _set_progress(self, db: AsyncSession, a: VideoAnalysis, progress: float):
        a.progress = progress
        await db.commit()
        await self._broadcast_progress(a)

    async def _fail(self, db: AsyncSession, a: VideoAnalysis, msg: str):
        """将分析任务标记为失败。"""
        a.status = AnalysisStatus.FAILED.value
        a.stage = "失败"
        a.error_message = msg
        a.finished_at = beijing_now()
        await db.commit()
        await self._broadcast_progress(a)

    async def _broadcast_progress(self, a: VideoAnalysis):
        """通过回调广播分析进度。"""
        if self._broadcast:
            try:
                await self._broadcast({
                    "id": a.id,
                    "download_task_id": a.download_task_id,
                    "status": a.status,
                    "stage": a.stage,
                    "progress": a.progress,
                    "error_message": a.error_message,
                })
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")


# 全局单例
analysis_engine = AnalysisEngine()
