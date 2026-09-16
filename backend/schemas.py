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
    knowledge_summary: str = ""
    enabled_tasks: list[str] = []
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
    # 启用的 LLM 分析项 key 列表；缺省 None 表示默认启用全部。
    # 取值：summary / script / knowledge
    enabled_tasks: Optional[list[str]] = None


class AnalysisBatchCreateRequest(BaseModel):
    download_task_ids: list[int] = Field(..., min_length=1, max_length=200, description="下载任务 id 列表")
    enabled_tasks: Optional[list[str]] = None


class AnalysisDigestRequest(BaseModel):
    """跨视频"大总结"请求。"""
    analysis_ids: list[int] = Field(..., min_length=2, max_length=50, description="参与聚合的分析任务 id 列表")
    # 聚合哪个单项总结：summary→content_summary / knowledge→knowledge_summary
    task: str = Field(default="summary", description="聚合维度：summary | knowledge")


class AnalysisDigestResponse(BaseModel):
    """跨视频"大总结"响应（不落库，仅返回生成结果）。"""
    digest: str
    source_count: int        # 实际参与聚合的视频数（去掉空内容后）
    llm_model: str


class ChatMessage(BaseModel):
    """单条对话消息（OpenAI 兼容格式）。"""
    role: str = Field(..., description="user | assistant")
    content: str


class AnalysisChatRequest(BaseModel):
    """对选中视频的总结内容进行多轮对话。"""
    analysis_ids: list[int] = Field(..., min_length=1, max_length=50, description="作为上下文的分析任务 id 列表")
    task: str = Field(default="summary", description="对话上下文维度：summary | knowledge")
    messages: list[ChatMessage] = Field(..., min_length=1, description="历史问答消息（最后一条为本次提问）")


class AnalysisChatResponse(BaseModel):
    """对话响应（不落库）。"""
    answer: str
    llm_model: str


class AnalysisBatchDetailRequest(BaseModel):
    """按 id 列表批量取分析详情（用于对比页跨页取数）。"""
    analysis_ids: list[int] = Field(..., min_length=1, max_length=50)


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


# ---- Analysis Candidates（"选择要分析的视频"专用列表）----
# analysis_status 取值：none(未分析) | pending | analyzing | completed | failed
# 其中 "analyzing" 为前端聚合态，对应后端 pending/extracting/transcribing/analyzing 任意一种。
class AnalysisCandidateResponse(BaseModel):
    download_task_id: int
    content_snapshot_id: int
    platform: str
    content_id: str
    title: str
    file_size: int = 0
    created_at: datetime | None = None
    analysis_id: int | None = None          # 无分析记录时为 None
    analysis_status: str = "none"           # "none"|"pending"|"analyzing"|"completed"|"failed"
    analysis_progress: float = 0

    model_config = {"from_attributes": True}


class AnalysisCandidateListResponse(BaseModel):
    total: int
    items: list[AnalysisCandidateResponse]


# ---- 竞品调研（Competitor Research）----
class CompetitorCreate(BaseModel):
    platform: Platform
    # 支持三种格式：主页链接 / 平台 uid（抖音 sec_uid、B站 mid）
    platform_uid: str = Field(..., min_length=1, max_length=500, description="主页链接 / 抖音 sec_uid / B站 mid")
    name: str = Field(default="", max_length=255, description="自定义备注名（如：竞品A-XX品牌）")
    note: str = ""


class CompetitorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    note: str | None = None
    enabled: bool | None = None


class CompetitorResponse(BaseModel):
    id: int
    name: str
    platform: Platform
    platform_uid: str
    homepage_url: str
    nickname: str
    note: str
    enabled: bool
    follower_count: int
    last_researched_at: Optional[datetime] = None
    extra_info: dict = {}
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CompetitorListResponse(BaseModel):
    total: int
    items: list[CompetitorResponse]


class DiscoverRequest(BaseModel):
    platform: Platform = Field(default=Platform.BILIBILI, description="发现渠道：bilibili 纯 API；douyin 需登录")
    keyword: str = Field(..., min_length=1, max_length=100)
    max_items: int = Field(default=20, ge=5, le=60, description="返回的候选账号数上限")
    max_pages: int = Field(default=3, ge=1, le=10, description="搜索翻页深度（B站每页42条；抖音控制滚动批数）")


class DiscoveredCompetitor(BaseModel):
    """关键词发现的候选竞品账号。"""
    platform: str
    platform_uid: str
    nickname: str = ""
    signature: str = ""
    follower_count: int = 0
    video_hits: int = 0                 # 搜索结果中该账号出现的视频数
    latest_pub_time: str = ""           # 命中视频的最新发布日期 YYYY-MM-DD
    total_plays: int = 0                # 命中视频播放量合计
    sample_titles: list[str] = []       # 命中视频标题样例
    homepage_url: str = ""


class DiscoverResponse(BaseModel):
    platform: str
    keyword: str
    candidates: list[DiscoveredCompetitor]
    message: str = ""                   # 异常/降级说明（如抖音未登录）


class ResearchCreateRequest(BaseModel):
    title: str = Field(default="", max_length=255)
    competitor_ids: list[int] = Field(..., min_length=1, max_length=20)
    date_from: str | None = Field(default=None, description="起始日期 YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="截止日期 YYYY-MM-DD")
    max_videos_per_competitor: int = Field(default=8, ge=1, le=50, description="每竞品抓取的视频数")
    top_videos_for_comments: int = Field(default=5, ge=0, le=10, description="每竞品爬评论的爆款视频数")
    max_comments_per_video: int = Field(default=50, ge=1, le=500, description="每视频抓取的评论数")


class ResearchTaskResponse(BaseModel):
    id: int
    title: str
    status: str
    stage: str
    progress: float
    competitor_ids: list[int] = []
    competitor_count: int = 0
    params: dict = {}
    market_overview: str = ""
    markdown_path: str = ""
    trigger_type: str = "manual"
    error_message: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ResearchListResponse(BaseModel):
    total: int
    items: list[ResearchTaskResponse]


class ResearchScheduleUpsert(BaseModel):
    interval_minutes: int = Field(default=10080, ge=30, le=43200)
    enabled: bool = False
    params: dict = Field(
        default={},
        description="调研参数覆盖：days(回看天数)/max_videos_per_competitor/"
                    "top_videos_for_comments/max_comments_per_video/title_prefix",
    )


class ResearchScheduleResponse(BaseModel):
    id: int
    interval_minutes: int
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    params: dict = {}

    model_config = {"from_attributes": True}
