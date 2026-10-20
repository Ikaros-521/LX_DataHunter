"""ASR provider 抽象：本地 faster-whisper 与阿里云录音文件识别，可切换。

纯函数（segments_to_transcript / format_ts / *_dicts）便于单测；
provider 实现把音频文件转成 AsrResult。get_asr_provider 按 settings 选择。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from backend.config import settings

logger = logging.getLogger("lx_datahunter.asr")


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str


@dataclass
class AsrResult:
    segments: list[AsrSegment]
    language: str
    duration_sec: float


# ---- 纯函数 ----

def segments_to_transcript(segments: list[AsrSegment]) -> str:
    """把分段拼接成喂给 LLM 的纯文本。"""
    return "".join(s.text for s in segments).strip()


def format_ts(seconds: float) -> str:
    """秒数格式化为 m:ss 或 h:mm:ss。"""
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def segments_to_dicts(segments: list[AsrSegment]) -> list[dict]:
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


def dict_segments_to_objects(dicts: list[dict]) -> list[AsrSegment]:
    return [AsrSegment(d["start"], d["end"], d["text"]) for d in dicts]


# ---- provider 协议 ----

class AsrProvider(Protocol):
    name: str

    async def transcribe(self, audio_path: str) -> AsrResult: ...


# ---- 本地 faster-whisper ----

class FasterWhisperProvider:
    """基于 faster-whisper（ctranslate2）的本地 ASR。

    模型懒加载为进程级单例；transcribe 是同步且 CPU/GPU 密集，丢到执行器里
    跑，避免阻塞事件循环。默认中文、带 VAD。
    """
    name = "local"
    _model = None

    @classmethod
    def _load_model(cls):
        if cls._model is None:
            from faster_whisper import WhisperModel  # 延迟导入，避免未装时影响其它功能
            compute = settings.whisper_compute_type or "auto"

            def _build(local_only: bool) -> "WhisperModel":
                return WhisperModel(
                    settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=compute,
                    local_files_only=local_only,
                )

            try:
                # 优先本地缓存：命中则零网络请求，不受代理/HF 可达性影响
                cls._model = _build(True)
            except Exception as exc:
                logger.info(
                    f"whisper model '{settings.whisper_model}' 不在本地缓存"
                    f"（{exc.__class__.__name__}），回退到从 HF 下载…"
                )
                # 缓存缺失才走网络；下载源受 HF_ENDPOINT 环境变量控制（可用 hf-mirror）
                cls._model = _build(False)
            logger.info(f"whisper model loaded: {settings.whisper_model} on {settings.whisper_device}")
        return cls._model

    async def transcribe(self, audio_path: str) -> AsrResult:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._transcribe_sync, audio_path
        )

    def _transcribe_sync(self, audio_path: str) -> AsrResult:
        model = self._load_model()
        segments_gen, info = model.transcribe(
            audio_path, language="zh", vad_filter=True
        )
        segs = [
            AsrSegment(s.start, s.end, s.text.strip())
            for s in segments_gen
        ]
        return AsrResult(segs, getattr(info, "language", "zh"), getattr(info, "duration", 0.0))


# ---- 阿里云录音文件识别 ----

class AliyunAsrProvider:
    """阿里云 NLS 录音文件识别（httpx 直连，不引重型 SDK）。

    v1 实现：调用录音文件识别 REST 接口。需要 AccessKey/AppKey。
    注意：录音文件识别通常要求音频在可公网访问的 URL/OSS 上；若环境不便，
    可在实现时切换到支持本地文件上传的"极速版"。此处给出占位实现与
    清晰错误，真实集成在首次接入阿里云时补全（不阻塞本地 provider）。
    """
    name = "aliyun"

    def __init__(self, access_key: str, secret_key: str, app_key: str):
        if not (access_key and secret_key and app_key):
            raise RuntimeError("阿里云 ASR 缺少凭证：请配置 ALIYUN_ASR_ACCESS_KEY/SECRET_KEY/APP_KEY")
        self.access_key = access_key
        self.secret_key = secret_key
        self.app_key = app_key

    async def transcribe(self, audio_path: str) -> AsrResult:
        raise NotImplementedError(
            "阿里云录音文件识别待接入：需确定音频投递方式（OSS/公网 URL 或极速版本地上传）。"
            "v1 请使用 local provider。"
        )


# ---- 选择器 ----

def get_asr_provider() -> AsrProvider:
    """按 settings.asr_provider 返回 provider 实例。"""
    if settings.asr_provider == "aliyun":
        return AliyunAsrProvider(
            settings.aliyun_asr_access_key,
            settings.aliyun_asr_secret_key,
            settings.aliyun_asr_app_key,
        )
    return FasterWhisperProvider()
