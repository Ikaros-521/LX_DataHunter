"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from .models import Platform, TriggerType, SessionStatus, ContentType

# ---- Creator ----
class CreatorCreate(BaseModel):
    platform: Platform
    platform_uid: str = Field(..., min_length=1, max_length=255)

class CreatorInfo(BaseModel):
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    follower_count: int = 0

class CreatorResponse(BaseModel):
    id: int
    platform: Platform
    platform_uid: str
    nickname: str
    signature: str
    avatar_url: str
    follower_count: int
    extra_info: dict = {}
    created_at: datetime
    updated_at: datetime
    latest_stats: Optional[dict] = None  # from latest session
    schedule: Optional["ScheduleResponse"] = None

    model_config = {"from_attributes": True}

class CreatorListResponse(BaseModel):
    total: int
    items: list[CreatorResponse]

# ---- Session ----
class SessionResponse(BaseModel):
    id: int
    creator_id: int
    platform: Platform
    trigger_type: TriggerType
    status: SessionStatus
    error_message: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None
    total_contents: int = 0
    engagement_total: dict = {}
    delta_from_prev: dict = {}
    creator_nickname: str = ""
    keyword: str = ""

    model_config = {"from_attributes": True}

class SessionListResponse(BaseModel):
    total: int
    items: list[SessionResponse]

# ---- Content ----
class ContentResponse(BaseModel):
    id: int
    session_id: int
    content_id: str
    content_type: ContentType
    title: str
    cover_url: str
    create_time: Optional[datetime]
    duration_sec: float
    url: str
    tags: list[str] = []
    music_title: str = ""
    likes: int
    comments: int
    shares: int
    plays: int
    collects: int
    extra_data: dict = {}
    platform: str = ""
    creator_nickname: str = ""

    model_config = {"from_attributes": True}

class ContentListResponse(BaseModel):
    total: int
    items: list[ContentResponse]

# ---- Schedule ----
class ScheduleUpsert(BaseModel):
    interval_minutes: int = Field(..., ge=5, le=10080)  # 5min ~ 1 week
    enabled: bool = True
    keyword: str | None = None  # 搜索类任务的关键词
    max_items: int = 50  # 每次爬取数量

class ScheduleResponse(BaseModel):
    id: int
    creator_id: int
    interval_minutes: int
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    extra_config: Optional[dict] = None

    model_config = {"from_attributes": True}

# ---- Platform ----
class PlatformInfo(BaseModel):
    id: str
    name: str
    icon: str = ""
    status: str = "active"  # active / coming_soon

# ---- Trigger ----
class CrawlOptions(BaseModel):
    max_items: int | None = Field(default=50, ge=1, le=500, description="最大爬取条数")
    date_from: str | None = Field(default=None, description="起始日期 YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="截止日期 YYYY-MM-DD")

class TriggerCrawlResponse(BaseModel):
    session_id: int
    message: str

# ---- Credential ----
class CredentialCreate(BaseModel):
    platform: str
    name: str = ""
    cookie_str: str

class CredentialResponse(BaseModel):
    id: int
    platform: str
    name: str
    cookie_str: str  # 前端需要完整 cookie 才能编辑
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

# ---- Common ----
class MessageResponse(BaseModel):
    message: str

class BatchDeleteRequest(BaseModel):
    session_ids: list[int]

# ---- Download ----
class DownloadBatchCreate(BaseModel):
    content_ids: list[int] = Field(..., min_length=1, max_length=200, description="content_snapshots 的 id 列表")

class DownloadTaskResponse(BaseModel):
    id: int
    content_snapshot_id: int
    platform: str
    content_id: str
    title: str
    status: str
    progress: float
    file_path: str
    file_size: int
    error_message: str = ""
    retry_count: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    url: str = ""

    model_config = {"from_attributes": True}

class DownloadListResponse(BaseModel):
    total: int
    items: list[DownloadTaskResponse]

class DownloadBatchDeleteRequest(BaseModel):
    task_ids: list[int]

class DownloadStatsResponse(BaseModel):
    total: int = 0
    pending: int = 0
    downloading: int = 0
    completed: int = 0
    failed: int = 0
    total_size: int = 0  # bytes

class YtdlpCheckResponse(BaseModel):
    available: bool = False
    version: str = ""
    path: str = ""


# ---- Video Analysis ----
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
