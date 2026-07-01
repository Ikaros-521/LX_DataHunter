# 视频分析功能（v1：单视频分析）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为已下载视频追加 ASR 逐字稿（带时间轴）+ 两类大模型分析（脚本/拍摄/制作手法、内容总结），照抄下载引擎的异步队列/状态机/WebSocket 模式实现。

**Architecture:** 新增 `AnalysisEngine`（asyncio 队列+worker，与 `download_engine` 同构）、`VideoAnalysis` 表、可切换的 ASR provider（本地 faster-whisper / 阿里云）、OpenAI 兼容 `LlmClient`、`/api/v1/analysis/*` REST + `/ws/analysis` 进度。前端新增"视频分析"列表页与详情页。

**Tech Stack:** FastAPI、SQLAlchemy(async)、faster-whisper(ctranslate2)、ffmpeg、httpx、React+Antd+react-markdown、Vite。

参考规格：`docs/superpowers/specs/2026-07-01-video-analysis-design.md`

---

## 约定

- 后端测试只对**纯函数/provider（mock）/client（mock）**做单测，走 `pytest`（已配 `pytest.ini`，`pythonpath=.`）。引擎管线与 API 的端到端由用户本地验证（与下载功能一致的做法）。
- 前端无测试框架，验证= `npm run build` 通过 + 用户手测。
- 每个任务结束提交一次，提交信息中文，末尾带 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 运行 Python 用 `D:/miniconda3/envs/py310/python.exe`（项目环境）。
- 运行测试：`D:/miniconda3/envs/py310/python.exe -m pytest tests/<file> -v`。

## 文件结构

**后端新增**
- `backend/engine/asr.py` — `AsrSegment`/`AsrResult` 数据类、纯函数（`segments_to_transcript`/`format_ts`）、`AsrProvider` 协议、`FasterWhisperProvider`、`AliyunAsrProvider`、`get_asr_provider`。
- `backend/engine/llm_client.py` — `LlmClient`（OpenAI 兼容）+ prompt 模板 + 纯函数 `build_script_prompt`/`build_summary_prompt`。
- `backend/engine/analyzer.py` — `AnalysisEngine`（引擎）+ 全局单例 `analysis_engine`。
- `backend/api/analysis.py` — REST 路由。
- `backend/api/analysis_ws.py` — WebSocket 广播。

**后端修改**
- `backend/config.py` — 新增配置项。
- `backend/models.py` — `AnalysisStatus`、`VideoAnalysis`。
- `backend/schemas.py` — 分析相关 Pydantic schema。
- `backend/main.py` — lifespan 启停引擎、挂载路由/WS、`/analysis/check` 用就绪检测。
- `requirements.txt` — `faster-whisper`。

**前端新增**
- `frontend/src/hooks/useAnalysisProgress.ts`
- `frontend/src/pages/VideoAnalysis.tsx`
- `frontend/src/pages/VideoAnalysisDetail.tsx`

**前端修改**
- `frontend/src/api/queries.ts` — `VideoAnalysis` 类型 + hooks。
- `frontend/src/App.tsx` — 路由。
- `frontend/src/components/AppLayout.tsx` — 导航项。
- `frontend/package.json` — `react-markdown`。

**测试新增**
- `tests/test_asr.py`、`tests/test_llm_client.py`、`tests/test_analyzer.py`

---

## Phase 1 — 后端基础（配置 / 模型 / schema）

### Task 1: 新增配置项

**Files:** Modify `backend/config.py`

- [ ] **Step 1: 在 `Settings` 类中追加配置项**

把以下字段加到 `backend/config.py` 的 `Settings` 类里（紧跟现有字段，`model_config` 之前）：

```python
    # ---- 视频分析 ----
    asr_provider: str = "local"            # local | aliyun
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "auto"
    aliyun_asr_access_key: str = ""
    aliyun_asr_secret_key: str = ""
    aliyun_asr_app_key: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout: int = 120
    max_concurrent_analyses: int = 2
    max_analysis_retries: int = 3
```

- [ ] **Step 2: 验证可导入**

Run: `D:/miniconda3/envs/py310/python.exe -c "from backend.config import settings; print(settings.asr_provider, settings.llm_model, settings.max_concurrent_analyses)"`
Expected: `local deepseek-chat 2`

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat(analysis): 新增 ASR/LLM/分析引擎配置项

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 新增 VideoAnalysis 模型

**Files:** Modify `backend/models.py`; Test: `tests/test_models_analysis.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_models_analysis.py`：

```python
"""VideoAnalysis 模型基础断言。"""
from backend.models import VideoAnalysis, AnalysisStatus


def test_analysis_status_values():
    assert AnalysisStatus.PENDING.value == "pending"
    assert AnalysisStatus.EXTRACTING.value == "extracting"
    assert AnalysisStatus.TRANSCRIBING.value == "transcribing"
    assert AnalysisStatus.ANALYZING.value == "analyzing"
    assert AnalysisStatus.COMPLETED.value == "completed"
    assert AnalysisStatus.FAILED.value == "failed"


def test_video_analysis_table_columns():
    cols = {c.name for c in VideoAnalysis.__table__.columns}
    for required in {
        "id", "download_task_id", "content_snapshot_id", "platform",
        "content_id", "title", "status", "stage", "progress",
        "asr_provider", "asr_segments", "transcript", "duration_sec",
        "language", "script_analysis", "content_summary", "llm_model",
        "error_message", "retry_count", "created_at", "started_at",
        "finished_at",
    }:
        assert required in cols, f"missing column {required}"


def test_download_task_id_is_unique():
    col = VideoAnalysis.__table__.columns["download_task_id"]
    assert col.unique is True
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_models_analysis.py -v`
Expected: FAIL（`cannot import name 'VideoAnalysis'`）

- [ ] **Step 3: 实现模型**

在 `backend/models.py` 末尾（`PlatformCredential` 之后）追加：

```python
class AnalysisStatus(str, enum.Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoAnalysis(Base):
    """单个已下载视频的分析结果（与 download_task 1:1）。

    ASR 结果（asr_segments/transcript）与 LLM 结果（script_analysis/
    content_summary）分字段存储，便于重跑 LLM 时复用已缓存 ASR。
    """
    __tablename__ = "video_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    download_task_id = Column(Integer, ForeignKey("download_tasks.id"), nullable=False, unique=True, index=True)
    content_snapshot_id = Column(Integer, index=True)
    platform = Column(String(20), default="")
    content_id = Column(String(128), default="")
    title = Column(Text, default="")
    status = Column(String(20), nullable=False, default="pending", index=True)
    stage = Column(String(64), default="")          # 当前阶段中文文案
    progress = Column(Float, default=0)
    asr_provider = Column(String(20), default="")    # local | aliyun
    asr_segments = Column(JSON, default=list)        # [{start,end,text},...]
    transcript = Column(Text, default="")            # 拼接纯文本
    duration_sec = Column(Float, default=0)
    language = Column(String(16), default="")
    script_analysis = Column(Text, default="")       # 脚本+拍摄+制作手法（markdown）
    content_summary = Column(Text, default="")       # 内容总结（markdown）
    llm_model = Column(String(64), default="")
    error_message = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=beijing_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
```

> 注：lifespan 里用 `Base.metadata.create_all`，新表会自动建出，无需手写迁移。

- [ ] **Step 4: 运行测试，确认通过**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_models_analysis.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/test_models_analysis.py
git commit -m "feat(analysis): 新增 VideoAnalysis 模型与 AnalysisStatus

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 新增 Pydantic schema

**Files:** Modify `backend/schemas.py`

- [ ] **Step 1: 追加 schema**

在 `backend/schemas.py` 末尾追加：

```python
class VideoAnalysisBase(BaseModel):
    model_config = {"from_attributes": True}


class VideoAnalysisResponse(VideoAnalysisBase):
    id: int
    download_task_id: int
    content_snapshot_id: int
    platform: str
    content_id: str
    title: str
    status: str
    stage: str
    progress: float
    asr_provider: str
    asr_segments: list[dict]
    transcript: str
    duration_sec: float
    language: str
    script_analysis: str
    content_summary: str
    llm_model: str
    error_message: str
    retry_count: int
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AnalysisListResponse(BaseModel):
    total: int
    items: list[VideoAnalysisResponse]


class AnalysisCreateRequest(BaseModel):
    download_task_id: int


class AnalysisCheckResponse(BaseModel):
    ffmpeg_available: bool
    ffmpeg_path: str
    asr_provider: str
    asr_ready: bool
    asr_message: str
    llm_ready: bool
    llm_message: str


class AnalysisStatsResponse(BaseModel):
    total: int
    pending: int
    analyzing: int
    completed: int
    failed: int
```

> 若 `BaseModel`/`datetime` 未在文件顶部导入，补 `from pydantic import BaseModel` 与 `from datetime import datetime`（项目已有同类用法，照抄）。

- [ ] **Step 2: 验证可导入**

Run: `D:/miniconda3/envs/py310/python.exe -c "from backend.schemas import VideoAnalysisResponse, AnalysisCreateRequest, AnalysisCheckResponse; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/schemas.py
git commit -m "feat(analysis): 新增分析相关 Pydantic schema

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 2 — ASR provider 与 LLM client

### Task 4: ASR 纯函数 + provider 抽象 + 选择器

**Files:** Create `backend/engine/asr.py`; Test: `tests/test_asr.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_asr.py`：

```python
"""ASR 纯函数与 provider 选择器的单测（不碰真实模型/网络）。"""
import pytest

from backend.engine import asr
from backend.engine.asr import AsrResult, AsrSegment


def test_segments_to_transcript_concat_and_strip():
    segs = [AsrSegment(0, 1, "你好 "), AsrSegment(1, 2, "世界。 ")]
    assert asr.segments_to_transcript(segs) == "你好 世界。"


def test_format_ts_under_an_hour():
    assert asr.format_ts(0) == "0:00"
    assert asr.format_ts(65) == "1:05"
    assert asr.format_ts(3661) == "1:01:01"


def test_segments_to_dicts_roundtrip():
    segs = [AsrSegment(0.0, 1.5, "a")]
    d = asr.segments_to_dicts(segs)
    assert d == [{"start": 0.0, "end": 1.5, "text": "a"}]
    assert asr.dict_segments_to_objects(d) == segs


def test_get_asr_provider_local_by_default(monkeypatch):
    # 默认配置 asr_provider == "local"
    assert asr.get_asr_provider().name == "local"


def test_get_asr_provider_aliyun_requires_credentials(monkeypatch):
    monkeypatch.setattr(asr.settings, "asr_provider", "aliyun")
    monkeypatch.setattr(asr.settings, "aliyun_asr_access_key", "")
    with pytest.raises(RuntimeError):
        asr.get_asr_provider()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_asr.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 asr.py**

创建 `backend/engine/asr.py`：

```python
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

logger = logging.getLogger("fapai.asr")


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
            cls._model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=compute,
            )
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
            raise RuntimeError("阿里云 ASR 缺少凭证：请配置 FAPAI_ALIYUN_ASR_ACCESS_KEY/SECRET_KEY/APP_KEY")
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_asr.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/engine/asr.py tests/test_asr.py
git commit -m "feat(analysis): ASR provider 抽象（faster-whisper/阿里云）与纯函数

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: LLM client + prompt 构造

**Files:** Create `backend/engine/llm_client.py`; Test: `tests/test_llm_client.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_llm_client.py`：

```python
"""LLM prompt 构造与 client（mock httpx）的单测。"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.engine import llm_client
from backend.engine.llm_client import LlmClient, build_script_prompt, build_summary_prompt


TRANSCRIPT = "大家好，今天介绍一款产品。"


def test_build_script_prompt_contains_transcript_and_sections():
    sys_p, user_p = build_script_prompt(TRANSCRIPT)
    assert "短视频" in sys_p
    assert TRANSCRIPT in user_p
    # 要求覆盖脚本结构与拍摄制作手法
    assert "脚本" in user_p and "拍摄" in user_p and "制作" in user_p


def test_build_summary_prompt_contains_transcript():
    sys_p, user_p = build_summary_prompt(TRANSCRIPT)
    assert TRANSCRIPT in user_p
    assert "总结" in user_p


def test_llm_client_chat_returns_content():
    client = LlmClient(base_url="http://x", api_key="k", model="m", timeout=10)
    fake_resp = {
        "choices": [{"message": {"content": "分析结果"}}]
    }
    with patch.object(llm_client.httpx, "AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value = AsyncMock()
        instance.post = AsyncMock(return_value=AsyncMock(
            status_code=200,
            json=lambda: fake_resp,
            raise_for_status=lambda: None,
        ))
        # 重新构造：让 AsyncClient() 作为 async ctx
        am = AsyncMock()
        am.post = AsyncMock(return_value=_Resp(fake_resp))
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=am)
        MockClient.return_value = cm
        result = asyncio.get_event_loop().run_until_complete(client.chat("s", "u"))
    assert result == "分析结果"


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self._data
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_llm_client.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 llm_client.py**

创建 `backend/engine/llm_client.py`：

```python
"""OpenAI 兼容的大模型客户端 + 视频分析 prompt 模板。

用 httpx 直连 {base_url}/chat/completions，避免引入 openai SDK 重依赖。
"""
from __future__ import annotations

import logging

import httpx

from backend.config import settings

logger = logging.getLogger("fapai.llm")


def build_script_prompt(transcript: str) -> tuple[str, str]:
    """脚本结构 + 拍摄/制作手法分析的 (system, user) prompt。"""
    system = (
        "你是资深短视频编导与内容运营。基于视频的语音逐字稿，"
        "客观分析其脚本结构与拍摄/制作手法，输出结构化、可复用的结论。"
    )
    user = (
        "以下是视频的语音逐字稿：\n"
        f"{transcript}\n\n"
        "请从以下维度分析（用 markdown 小标题分节，每节给出要点）：\n"
        "1. 脚本结构：开头钩子、节奏、信息层次、卖点、引导互动(CTA)。\n"
        "2. 拍摄手法：镜头景别与运动、画面构图、出镜方式（口播/实拍/混剪）。\n"
        "3. 制作手法：剪辑节奏、字幕、音乐/音效、转场、包装元素。\n"
        "4. 可复用要点：3 条最值得借鉴的做法。\n"
        "只依据逐字稿能推断的内容，不要编造画面细节。"
    )
    return system, user


def build_summary_prompt(transcript: str) -> tuple[str, str]:
    """内容总结的 (system, user) prompt。"""
    system = "你是内容运营助手，擅长把视频内容凝练成简明摘要。"
    user = (
        "以下是视频的语音逐字稿：\n"
        f"{transcript}\n\n"
        "请总结这条视频（用 markdown）：先一句话概括主题，"
        "再列 3-5 个要点；如有明显目标人群或价值主张，单列一条。"
    )
    return system, user


class LlmClient:
    """OpenAI 兼容 /chat/completions 客户端。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "LlmClient":
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout,
        )

    async def chat(self, system: str, user: str) -> str:
        if not (self.base_url and self.api_key):
            raise RuntimeError("LLM 未配置：请设置 FAPAI_LLM_BASE_URL 与 FAPAI_LLM_API_KEY")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_llm_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/engine/llm_client.py tests/test_llm_client.py
git commit -m "feat(analysis): OpenAI 兼容 LLM 客户端与分析 prompt 模板

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 3 — 分析引擎

### Task 6: AnalysisEngine（队列/worker/管线）

**Files:** Create `backend/engine/analyzer.py`; Test: `tests/test_analyzer.py`

- [ ] **Step 1: 写失败测试（纯函数：进度区间映射）**

创建 `tests/test_analyzer.py`：

```python
"""AnalysisEngine 纯函数单测（不跑真实管线/DB/GPU/LLM）。"""
from backend.engine.analyzer import stage_progress


def test_stage_progress_start_of_transcribing():
    assert stage_progress("transcribing", 0.0) == 10.0


def test_stage_progress_end_of_transcribing():
    assert stage_progress("transcribing", 1.0) == 60.0


def test_stage_progress_analyzing_range():
    assert stage_progress("analyzing", 0.0) == 60.0
    assert stage_progress("analyzing", 1.0) == 99.0


def test_stage_progress_extracting():
    assert stage_progress("extracting", 0.0) == 0.0
    assert stage_progress("extracting", 1.0) == 10.0
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_analyzer.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 analyzer.py**

创建 `backend/engine/analyzer.py`：

```python
"""视频分析引擎 — 照抄 download_engine 的异步队列/状态机/进度模式。

管线：抽音频(ffmpeg) → ASR → 两类 LLM 分析。结果落 VideoAnalysis。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models import VideoAnalysis, AnalysisStatus, DownloadTask, DownloadStatus, beijing_now
from backend.engine.asr import (
    get_asr_provider, segments_to_transcript, segments_to_dicts, AsrProvider,
)
from backend.engine.llm_client import LlmClient, build_script_prompt, build_summary_prompt

logger = logging.getLogger("fapai.analyzer")

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
        # 恢复中断任务
        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(VideoAnalysis)
                .where(VideoAnalysis.status.in_([
                    AnalysisStatus.EXTRACTING.value,
                    AnalysisStatus.TRANSCRIBING.value,
                    AnalysisStatus.ANALYZING.value,
                ]))
                .values(status=AnalysisStatus.PENDING.value, error_message="Server restarted — analysis interrupted")
            )
            await db.commit()
            result = await db.execute(
                select(VideoAnalysis.id).where(VideoAnalysis.status == AnalysisStatus.PENDING.value)
            )
            for aid in result.scalars().all():
                self._queue.put_nowait(aid)

        self._semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
        self._running = True
        for i in range(settings.max_concurrent_analyses):
            self._workers.append(asyncio.create_task(self._worker(i)))
        logger.info(f"Analysis engine started: {settings.max_concurrent_analyses} workers")

    async def stop(self):
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
        async with AsyncSessionLocal() as db:
            a = await db.get(VideoAnalysis, analysis_id)
            if not a:
                logger.warning(f"Analysis {analysis_id} not found")
                return
            if a.status not in (AnalysisStatus.PENDING.value, AnalysisStatus.FAILED.value):
                return

            dtask = await db.get(DownloadTask, a.download_task_id)
            if not dtask or dtask.status != DownloadStatus.COMPLETED.value or not dtask.file_path or not os.path.exists(dtask.file_path):
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

                # 3. LLM 分析（两类并发）
                a.status = AnalysisStatus.ANALYZING.value
                a.stage = "大模型分析"
                await db.commit()
                await self._broadcast_progress(a)

                client = LlmClient.from_settings()
                s_sys, s_user = build_script_prompt(a.transcript)
                m_sys, m_user = build_summary_prompt(a.transcript)
                script, summary = await asyncio.gather(
                    client.chat(s_sys, s_user),
                    client.chat(m_sys, m_user),
                )
                a.script_analysis = script
                a.content_summary = summary
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
                msg = str(e)[:500]
                logger.error(f"Analysis failed: {analysis_id}, error={msg}")
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
        out = tempfile.mktemp(prefix="fapai_audio_", suffix=".wav")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
            out,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", "replace")[-500:]
            raise RuntimeError(f"ffmpeg 抽音频失败: {err}")
        return out

    async def _set_progress(self, db: AsyncSession, a: VideoAnalysis, progress: float):
        a.progress = progress
        await db.commit()
        await self._broadcast_progress(a)

    async def _fail(self, db: AsyncSession, a: VideoAnalysis, msg: str):
        a.status = AnalysisStatus.FAILED.value
        a.stage = "失败"
        a.error_message = msg
        a.finished_at = beijing_now()
        await db.commit()
        await self._broadcast_progress(a)

    async def _broadcast_progress(self, a: VideoAnalysis):
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `D:/miniconda3/envs/py310/python.exe -m pytest tests/test_analyzer.py -v`
Expected: 4 passed

- [ ] **Step 5: 验证模块导入**

Run: `D:/miniconda3/envs/py310/python.exe -c "from backend.engine.analyzer import analysis_engine, AnalysisEngine, stage_progress; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add backend/engine/analyzer.py tests/test_analyzer.py
git commit -m "feat(analysis): AnalysisEngine 异步管线（抽音频→ASR→LLM）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 4 — REST API + WebSocket

### Task 7: 分析 REST API

**Files:** Create `backend/api/analysis.py`

- [ ] **Step 1: 实现路由**

创建 `backend/api/analysis.py`：

```python
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


@router.get("/check", response_model=AnalysisCheckResponse)
async def check_analysis():
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


@router.post("", response_model=VideoAnalysisResponse, status_code=202)
async def create_analysis(body: AnalysisCreateRequest, db: AsyncSession = Depends(get_db)):
    dtask = await db.get(DownloadTask, body.download_task_id)
    if not dtask:
        raise HTTPException(404, f"下载任务不存在: id={body.download_task_id}")
    if dtask.status != DownloadStatus.COMPLETED.value:
        raise HTTPException(400, "仅可分析已完成的下载")
    if not dtask.file_path:
        raise HTTPException(400, "该下载没有本地文件")

    snapshot = await db.get(ContentSnapshot, dtask.content_snapshot_id) if dtask.content_snapshot_id else None

    # 1:1：已存在则就地重跑
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
        a.status = AnalysisStatus.PENDING.value
        a.error_message = ""
        a.retry_count = 0
        a.script_analysis = ""
        a.content_summary = ""
    await db.commit()
    await db.refresh(a)
    await analysis_engine.enqueue(a.id)
    return _to_response(a)


@router.get("", response_model=AnalysisListResponse)
async def list_analysis(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(VideoAnalysis).order_by(VideoAnalysis.id.desc())
    if status:
        stmt = stmt.where(VideoAnalysis.status == status)
    if platform:
        stmt = stmt.where(VideoAnalysis.platform == platform)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    tasks = (await db.execute(stmt)).scalars().all()
    return AnalysisListResponse(total=total, items=[_to_response(t) for t in tasks])


@router.get("/stats", response_model=AnalysisStatsResponse)
async def analysis_stats(db: AsyncSession = Depends(get_db)):
    async def count(status_value: str) -> int:
        return (await db.execute(
            select(func.count(VideoAnalysis.id)).where(VideoAnalysis.status == status_value)
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


@router.get("/{analysis_id}", response_model=VideoAnalysisResponse)
async def get_analysis(analysis_id: int, db: AsyncSession = Depends(get_db)):
    a = await db.get(VideoAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, f"分析任务不存在: id={analysis_id}")
    return _to_response(a)


@router.post("/{analysis_id}/retry", status_code=202)
async def retry_analysis(
    analysis_id: int, force_asr: bool = False, db: AsyncSession = Depends(get_db)
):
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


@router.delete("/{analysis_id}")
async def delete_analysis(analysis_id: int, db: AsyncSession = Depends(get_db)):
    a = await db.get(VideoAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, f"分析任务不存在: id={analysis_id}")
    await db.delete(a)
    await db.commit()
    return {"message": f"已删除分析记录: analysis_id={analysis_id}"}
```

- [ ] **Step 2: 验证导入与路由**

Run: `D:/miniconda3/envs/py310/python.exe -c "import backend.api.analysis as m; print([r.path for r in m.router.routes])"`
Expected: 列出 `/api/v1/analysis`、`/api/v1/analysis/check`、`/api/v1/analysis/{analysis_id}` 等。

- [ ] **Step 3: Commit**

```bash
git add backend/api/analysis.py
git commit -m "feat(analysis): 分析任务 REST API（建/列/详情/重试/删/就绪检测）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 分析 WebSocket

**Files:** Create `backend/api/analysis_ws.py`

- [ ] **Step 1: 实现 WS（照抄 download_ws.py 结构）**

先读 `backend/api/download_ws.py` 确认广播函数签名风格，然后创建 `backend/api/analysis_ws.py`：

```python
"""视频分析进度 WebSocket。结构同 download_ws.py。"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket

logger = logging.getLogger("fapai.api.analysis_ws")
router = APIRouter()

_clients: set[WebSocket] = set()


async def broadcast_analysis_progress(payload: dict):
    """供 AnalysisEngine 调用的广播回调。"""
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


@router.websocket("/ws/analysis")
async def analysis_ws(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        # 接收心跳，忽略内容
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        _clients.discard(websocket)
```

> 若 `download_ws.py` 的广播函数名/心跳约定不同，照其风格对齐（保持一致即可）。

- [ ] **Step 2: 验证导入**

Run: `D:/miniconda3/envs/py310/python.exe -c "import backend.api.analysis_ws as m; print('ok', [r.path for r in m.router.routes])"`
Expected: `ok ['/ws/analysis']`

- [ ] **Step 3: Commit**

```bash
git add backend/api/analysis_ws.py
git commit -m "feat(analysis): 分析进度 WebSocket /ws/analysis

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 5 — 接入 main.py

### Task 9: 在 main.py 挂载引擎/路由/WS

**Files:** Modify `backend/main.py`

- [ ] **Step 1: 导入路由**

在 `backend/main.py` 顶部路由 import 区（与 `downloads_router` 同处）追加：

```python
from .api.analysis import router as analysis_router
from .api.analysis_ws import router as analysis_ws_router, broadcast_analysis_progress
```

并在 `app.include_router(...)` 区追加：

```python
app.include_router(analysis_router)
app.include_router(analysis_ws_router)
```

- [ ] **Step 2: lifespan 启停引擎**

在 `lifespan` 中"启动下载引擎"段之后、`yield` 之前，加入分析引擎启停。`yield` 之后加 `stop`。完整片段（替换/插入到对应位置）：

启动（`await download_engine.start()` 之后）：

```python
    # ---- 启动分析引擎 ----
    from .engine.analyzer import analysis_engine
    analysis_engine.set_broadcast(broadcast_analysis_progress)
    await analysis_engine.start()
    logger.info("Analysis engine initialized")
```

停止（`yield` 之后，`await download_engine.stop()` 之后）：

```python
    await analysis_engine.stop()
```

- [ ] **Step 3: 验证可启动**

Run: `D:/miniconda3/envs/py310/python.exe -c "import backend.main; print('app ok')"`
Expected: `app ok`（不报错）

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "feat(analysis): 在 lifespan 挂载分析引擎与路由/WS

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 后端依赖 + 烟雾测试

**Files:** Modify `requirements.txt`

- [ ] **Step 1: 加依赖**

在 `requirements.txt` 追加：

```
faster-whisper
```

- [ ] **Step 2: 安装（用户机器有 GPU，按 CUDA 装法）**

提示用户执行（一次性）：
```bash
pip install faster-whisper
# GPU 需要 CUDA：按 ctranslate2 文档装对应 nvidia-* 轮子，或确保本机有 CUDA 12.x
```
（本步骤不需在计划里跑，记录在"用户安装"里。）

- [ ] **Step 3: 启动后端做烟雾测试**

Run: `python run.py`（或 `python -m uvicorn backend.main:app --port 8000`，**不带 --reload**）
访问 `http://localhost:8000/api/v1/analysis/check`，预期返回 JSON：`ffmpeg_available`、`asr_ready`、`llm_ready`（按本机配置 true/false）。

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat(analysis): 依赖 faster-whisper

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 6 — 前端

> 前端无测试框架；每个任务以 `npm run build` 通过 + 用户手测为准。

### Task 11: API 类型与 hooks

**Files:** Modify `frontend/src/api/queries.ts`; Create `frontend/src/hooks/useAnalysisProgress.ts`

- [ ] **Step 1: 在 queries.ts 加类型与 hooks**

在 `frontend/src/api/queries.ts` 的 `// ---- Download ----` 段之后追加：

```ts
// ---- Analysis ----
export interface VideoAnalysis {
  id: number;
  download_task_id: number;
  content_snapshot_id: number;
  platform: string;
  content_id: string;
  title: string;
  status: 'pending' | 'extracting' | 'transcribing' | 'analyzing' | 'completed' | 'failed';
  stage: string;
  progress: number;
  asr_provider: string;
  asr_segments: { start: number; end: number; text: string }[];
  transcript: string;
  duration_sec: number;
  language: string;
  script_analysis: string;
  content_summary: string;
  llm_model: string;
  error_message: string;
  retry_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AnalysisCheck {
  ffmpeg_available: boolean;
  ffmpeg_path: string;
  asr_provider: string;
  asr_ready: boolean;
  asr_message: string;
  llm_ready: boolean;
  llm_message: string;
}

export function useAnalysisList(params?: Record<string, any>) {
  return useQuery({
    queryKey: ['analysis', params],
    queryFn: () => client.get('/analysis', { params }).then(r => r.data),
    refetchInterval: (q: any) => {
      const items: VideoAnalysis[] = q.state?.data?.items || [];
      return items.some(t => ['pending', 'extracting', 'transcribing', 'analyzing'].includes(t.status)) ? 3000 : false;
    },
  });
}

export function useAnalysisDetail(id: number | string) {
  return useQuery({
    queryKey: ['analysis', 'detail', id],
    queryFn: () => client.get(`/analysis/${id}`).then(r => r.data as VideoAnalysis),
    refetchInterval: (q: any) =>
      ['pending', 'extracting', 'transcribing', 'analyzing'].includes(q.state?.data?.status) ? 2000 : false,
  });
}

export function useCreateAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (download_task_id: number) => client.post('/analysis', { download_task_id }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

export function useRetryAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force_asr = false }: { id: number; force_asr?: boolean }) =>
      client.post(`/analysis/${id}/retry`, null, { params: { force_asr } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

export function useDeleteAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/analysis/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

export function useAnalysisCheck() {
  return useQuery({ queryKey: ['analysis', 'check'], queryFn: () => client.get('/analysis/check').then(r => r.data as AnalysisCheck) });
}
```

- [ ] **Step 2: 创建 useAnalysisProgress.ts**

照 `useDownloadProgress.ts` 结构，创建 `frontend/src/hooks/useAnalysisProgress.ts`：

```ts
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { VideoAnalysis } from '../api/queries';

/** 订阅 /ws/analysis，实时更新分析任务缓存。 */
export default function useAnalysisProgress() {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/analysis`;
    let reconnect: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          update(data);
        } catch { /* ignore */ }
      };
      ws.onclose = () => { reconnect = setTimeout(connect, 3000); };
      ws.onerror = () => ws.close();
    }

    function update(patch: Partial<VideoAnalysis> & { id: number }) {
      queryClient.setQueriesData<{ items: VideoAnalysis[] }>(
        { queryKey: ['analysis'] },
        (old) => old ? { ...old, items: old.items.map(t => t.id === patch.id ? { ...t, ...patch } : t) } : old,
      );
      queryClient.setQueriesData<VideoAnalysis>(
        { queryKey: ['analysis', 'detail'] },
        (old) => old && old.id === patch.id ? { ...old, ...patch } : old,
      );
    }

    connect();
    return () => { clearTimeout(reconnect); wsRef.current?.close(); };
  }, [queryClient]);
}
```

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npm run build`
Expected: 通过（无类型错误）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/queries.ts frontend/src/hooks/useAnalysisProgress.ts
git commit -m "feat(analysis): 前端 API 类型/hooks 与进度 WS

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: 安装 react-markdown

**Files:** Modify `frontend/package.json`

- [ ] **Step 1: 安装**

Run: `cd frontend && npm install react-markdown`

- [ ] **Step 2: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "feat(analysis): 前端依赖 react-markdown

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: 分析列表页

**Files:** Create `frontend/src/pages/VideoAnalysis.tsx`

- [ ] **Step 1: 实现列表页**

创建 `frontend/src/pages/VideoAnalysis.tsx`（结构参考 `DownloadManager.tsx`）：

```tsx
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Table, Tag, Button, Space, Progress, Modal, message, Alert, Tooltip,
} from 'antd';
import {
  AnalysisOutlined, ReloadOutlined, DeleteOutlined, PlusOutlined,
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, ClockCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  useAnalysisList, useAnalysisCheck, useCreateAnalysis,
  useRetryAnalysis, useDeleteAnalysis,
  useDownloadTasks,
  type VideoAnalysis, type DownloadTask,
} from '../api/queries';
import PlatformTag from '../components/PlatformTag';
import useAnalysisProgress from '../hooks/useAnalysisProgress';

const STATUS_CFG: Record<string, { color: string; text: string; icon: React.ReactNode }> = {
  pending: { color: 'default', text: '排队中', icon: <ClockCircleOutlined /> },
  extracting: { color: 'processing', text: '抽音频', icon: <LoadingOutlined /> },
  transcribing: { color: 'processing', text: '识别中', icon: <LoadingOutlined /> },
  analyzing: { color: 'processing', text: '分析中', icon: <LoadingOutlined /> },
  completed: { color: 'success', text: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: 'error', text: '失败', icon: <CloseCircleOutlined /> },
};

export default function VideoAnalysisPage() {
  useAnalysisProgress();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pickerOpen, setPickerOpen] = useState(false);

  const { data, isLoading } = useAnalysisList({ page, page_size: 20 });
  const { data: check } = useAnalysisCheck();
  const createMut = useCreateAnalysis();
  const retryMut = useRetryAnalysis();
  const deleteMut = useDeleteAnalysis();
  // 已下载完成、用于"新建分析"选择
  const { data: downloadsData } = useDownloadTasks({ status: 'completed', page: 1, page_size: 200 });

  const analyzedTaskIds = new Set((data?.items || []).map(a => a.download_task_id));
  const candidates: DownloadTask[] = (downloadsData?.items || []).filter(d => !analyzedTaskIds.has(d.id));

  const handleCreate = (downloadTaskId: number) => {
    createMut.mutate(downloadTaskId, {
      onSuccess: (res: any) => {
        message.success('已创建分析任务');
        setPickerOpen(false);
        navigate(`/analysis/${res.data.id}`);
      },
    });
  };

  const columns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (t: string) => t || '(无标题)' },
    { title: '平台', dataIndex: 'platform', key: 'platform', width: 80,
      render: (p: string) => <PlatformTag platform={p} /> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (s: string, r: VideoAnalysis) => {
        const cfg = STATUS_CFG[s] || { color: 'default', text: s, icon: null };
        return <Tooltip title={r.stage || ''}><Tag icon={cfg.icon} color={cfg.color}>{cfg.text}</Tag></Tooltip>;
      } },
    { title: '进度', dataIndex: 'progress', key: 'progress', width: 150,
      render: (p: number, r: VideoAnalysis) => r.status === 'completed'
        ? <Tag color="success">100%</Tag>
        : r.status === 'failed' ? <Tag color="error">失败</Tag>
        : <Progress percent={Math.round(p)} size="small" status="active" /> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-' },
    { title: '操作', key: 'actions', width: 180,
      render: (_: any, r: VideoAnalysis) => (
        <Space size="small">
          <Button size="small" onClick={() => navigate(`/analysis/${r.id}`)}>查看</Button>
          {r.status === 'failed' && (
            <Tooltip title="重试">
              <Button size="small" icon={<ReloadOutlined />}
                onClick={() => retryMut.mutate({ id: r.id }, { onSuccess: () => message.success('已重新入队') })} />
            </Tooltip>
          )}
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => deleteMut.mutate(r.id, { onSuccess: () => message.success('已删除') })} />
        </Space>
      ) },
  ];

  return (
    <div>
      {check && (!check.ffmpeg_available || !check.asr_ready || !check.llm_ready) && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="分析功能未就绪"
          description={`ffmpeg: ${check.ffmpeg_available ? '✓' : '✗ 未安装'} | ASR(${check.asr_provider}): ${check.asr_ready ? '✓' : '✗ ' + check.asr_message} | LLM: ${check.llm_ready ? '✓' : '✗ ' + check.llm_message}`} />
      )}

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setPickerOpen(true)}>新建分析</Button>
        <Tag icon={<AnalysisOutlined />}>视频分析</Tag>
      </Space>

      <Table dataSource={data?.items || []} columns={columns} rowKey="id" loading={isLoading} size="small"
        pagination={data?.total ? { current: page, total: data.total, pageSize: 20,
          onChange: p => setPage(p) } : false}
        locale={{ emptyText: '暂无分析任务' }} />

      <Modal open={pickerOpen} title="选择要分析的视频" onCancel={() => setPickerOpen(false)} footer={null} width={640}>
        <Table dataSource={candidates} rowKey="id" size="small" pagination={false}
          locale={{ emptyText: '没有"已下载完成且未分析"的视频' }}
          columns={[
            { title: '标题', dataIndex: 'title', ellipsis: true, render: (t: string) => t || '(无标题)' },
            { title: '平台', dataIndex: 'platform', width: 80, render: (p: string) => <PlatformTag platform={p} /> },
            { title: '操作', width: 90, render: (_: any, d: DownloadTask) =>
              <Button size="small" type="primary" onClick={() => handleCreate(d.id)}>分析</Button> },
          ]} />
      </Modal>
    </div>
  );
}
```

> 若 `useDownloadTasks` 的返回结构或 `DownloadTask` 字段名与实际不符，以 `queries.ts` 实际导出为准对齐。

- [ ] **Step 2: 构建验证**

Run: `cd frontend && npm run build`
Expected: 通过

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/VideoAnalysis.tsx
git commit -m "feat(analysis): 视频分析列表页 + 新建分析选择器

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: 分析详情页（逐字稿时间轴 + 视频联动 + 两类分析）

**Files:** Create `frontend/src/pages/VideoAnalysisDetail.tsx`

- [ ] **Step 1: 实现详情页**

创建 `frontend/src/pages/VideoAnalysisDetail.tsx`：

```tsx
import { useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Descriptions, Progress, Space, Tabs, Tag, Tooltip, message, Alert } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import {
  useAnalysisDetail, useRetryAnalysis, useDeleteAnalysis,
  type VideoAnalysis,
} from '../api/queries';
import useAnalysisProgress from '../hooks/useAnalysisProgress';
import PlatformTag from '../components/PlatformTag';

function fmt(seconds: number) {
  const t = Math.floor(seconds);
  const m = Math.floor(t / 60), s = t % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function VideoAnalysisDetail() {
  useAnalysisProgress();
  const { id } = useParams();
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement>(null);
  const { data: a, isLoading } = useAnalysisDetail(id!);
  const retryMut = useRetryAnalysis();
  const deleteMut = useDeleteAnalysis();

  if (isLoading || !a) return <div>加载中…</div>;

  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) { v.currentTime = t; v.play(); }
  };

  const inProgress = ['pending', 'extracting', 'transcribing', 'analyzing'].includes(a.status);

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/analysis')}>返回</Button>
        <Tooltip title="重跑（默认复用 ASR）">
          <Button icon={<ReloadOutlined />}
            onClick={() => retryMut.mutate({ id: a.id }, { onSuccess: () => message.success('已重新入队') })} />
        </Tooltip>
        <Button danger icon={<DeleteOutlined />}
          onClick={() => deleteMut.mutate(a.id, { onSuccess: () => { message.success('已删除'); navigate('/analysis'); } })} />
      </Space>

      <Descriptions size="small" column={3} bordered style={{ marginBottom: 16 }}
        title={<Space>{a.title || '(无标题)'} <PlatformTag platform={a.platform} /></Space>}>
        <Descriptions.Item label="状态"><Tag>{a.stage || a.status}</Tag></Descriptions.Item>
        <Descriptions.Item label="进度"><Progress percent={Math.round(a.progress)} size="small" /></Descriptions.Item>
        <Descriptions.Item label="ASR">{a.asr_provider || '-'}{a.language ? ` / ${a.language}` : ''}</Descriptions.Item>
      </Descriptions>

      {a.status === 'failed' && <Alert type="error" showIcon style={{ marginBottom: 16 }} message="分析失败" description={a.error_message} />}

      {inProgress ? (
        <Alert type="info" showIcon message={`分析进行中：${a.stage || a.status}`} description={<Progress percent={Math.round(a.progress)} />} />
      ) : (
        <Tabs defaultActiveKey="summary" items={[
          { key: 'summary', label: '内容总结', children: <ReactMarkdown>{a.content_summary || '（空）'}</ReactMarkdown> },
          { key: 'script', label: '脚本与制作手法', children: <ReactMarkdown>{a.script_analysis || '（空）'}</ReactMarkdown> },
          { key: 'transcript', label: `逐字稿 (${a.asr_segments.length})`, children: (
            <div>
              <video ref={videoRef} controls
                src={`/api/v1/downloads/${a.download_task_id}/file`}
                style={{ maxWidth: 480, width: '100%', marginBottom: 16, background: '#000' }} />
              <div style={{ maxHeight: '60vh', overflow: 'auto', fontFamily: 'monospace' }}>
                {a.asr_segments.map((seg, i) => (
                  <div key={i} style={{ padding: '4px 0', cursor: 'pointer', borderBottom: '1px solid #f0f0f0' }}
                    onClick={() => seek(seg.start)}>
                    <span style={{ color: '#1677ff', marginRight: 8 }}>[{fmt(seg.start)}]</span>
                    {seg.text}
                  </div>
                ))}
                {a.asr_segments.length === 0 && <div>（无逐字稿）</div>}
              </div>
            </div>
          ) },
        ]} />
      )}
    </div>
  );
}
```

- [ ] **Step 2: 构建验证**

Run: `cd frontend && npm run build`
Expected: 通过

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/VideoAnalysisDetail.tsx
git commit -m "feat(analysis): 视频分析详情页（时间轴+视频联动+两类分析）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 15: 路由 + 导航接入

**Files:** Modify `frontend/src/App.tsx`, `frontend/src/components/AppLayout.tsx`

- [ ] **Step 1: App.tsx 加路由**

在 `frontend/src/App.tsx` 顶部 import：

```ts
import VideoAnalysisPage from './pages/VideoAnalysis';
import VideoAnalysisDetail from './pages/VideoAnalysisDetail';
```

在 `<Routes>` 内（`/downloads` 之后）加：

```tsx
        <Route path="/analysis" element={<VideoAnalysisPage />} />
        <Route path="/analysis/:id" element={<VideoAnalysisDetail />} />
```

- [ ] **Step 2: AppLayout.tsx 加导航项**

读 `frontend/src/components/AppLayout.tsx`，在导航菜单数组中（"下载管理"之后）加一项，图标用 `AnalysisOutlined`（或与现有风格一致的图标），路径 `/analysis`，标签"视频分析"。示例（按实际菜单结构对齐字段名）：

```tsx
{ key: '/analysis', icon: <AnalysisOutlined />, label: '视频分析' },
```

> 若 `AnalysisOutlined` 不存在于项目 antd 图标集，改用已导入的图标（如 `PlayCircleOutlined`）。

- [ ] **Step 3: 构建并交付**

Run: `cd frontend && npm run build`
Expected: 通过，产物更新到 `backend/static/assets/`。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/AppLayout.tsx
git commit -m "feat(analysis): 接入视频分析路由与导航

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Phase 7 — 端到端验证（用户）

### Task 16: 配置 .env + 跑通单视频分析

- [ ] **Step 1: 写 .env**（项目根，按 CLAUDE.md 不入库）

```
FAPAI_LLM_BASE_URL=https://api.deepseek.com/v1   # 示例，按你的 provider 改
FAPAI_LLM_API_KEY=sk-xxx
FAPAI_LLM_MODEL=deepseek-chat
FAPAI_ASR_PROVIDER=local
FAPAI_WHISPER_MODEL=large-v3
FAPAI_WHISPER_DEVICE=cuda
```
确保 `.env` 在 `.gitignore`（CLAUDE.md：不提交密钥）。

- [ ] **Step 2: 装 faster-whisper + ffmpeg**

```bash
pip install faster-whisper
# ffmpeg：Windows 用 winget install Gyan.FFmpeg 或下载二进制加入 PATH
```
访问 `/api/v1/analysis/check` 确认三项都 ✓。

- [ ] **Step 3: 重启后端（不带 --reload）+ 浏览器强制刷新**

- [ ] **Step 4: 在"视频分析"页新建分析，观察进度，完成后查看详情（内容总结 / 脚本制作手法 / 逐字稿点跳视频）**

---

## Self-Review 结论

- **Spec 覆盖**：模型(T2)、provider(T4/T5)、引擎(T6)、API(T7)、WS(T8)、main 接入(T9)、配置(T1)、依赖(T10)、前端列表(T13)/详情(T14)/路由导航(T15)、.env 与端到端(T16) —— 规格各节均有任务覆盖。
- **阿里云 provider**：v1 给出占位 + 清晰 NotImplementedError（规格已声明"实现阶段定、不阻塞 v1"），本地 provider 可用。
- **类型一致性**：`VideoAnalysis` 字段、`stage_progress` 区间、`_to_response`、前端 `VideoAnalysis` 接口已对齐。
- **前端图标**：`AnalysisOutlined` 标注了回退方案。
- **测试**：纯函数/provider(mock)/client(mock) 有单测；管线与端到端由用户验证（与下载功能一致）。
