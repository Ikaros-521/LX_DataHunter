"""SQLAlchemy ORM models.  JSON 列使用 SQLAlchemy JSON 类型，自动序列化/反序列化。"""
import enum
from datetime import datetime, timezone, timedelta

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, JSON,
    DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship

# 北京时间 (UTC+8)
BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    """返回当前北京时间"""
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Platform(str, enum.Enum):
    DOUYIN = "douyin"
    BILIBILI = "bilibili"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"


class TriggerType(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class SessionStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentType(str, enum.Enum):
    VIDEO = "video"
    IMAGE = "image"
    ARTICLE = "article"


class MonitoredCreator(Base):
    __tablename__ = "monitored_creators"
    __table_args__ = (UniqueConstraint("platform", "platform_uid"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, index=True)
    platform_uid = Column(String(255), nullable=False, index=True)
    nickname = Column(String(255), default="")
    signature = Column(Text, default="")
    avatar_url = Column(String(500), default="")
    follower_count = Column(Integer, default=0)
    extra_info = Column(JSON, default=dict)
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)

    sessions = relationship("CrawlSession", back_populates="creator", cascade="all, delete-orphan")
    schedule = relationship("ScheduleConfig", back_populates="creator", uselist=False, cascade="all, delete-orphan")


class CrawlSession(Base):
    __tablename__ = "crawl_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    creator_id = Column(Integer, ForeignKey("monitored_creators.id"), nullable=False, index=True)
    platform = Column(String(20), nullable=False, index=True)
    trigger_type = Column(String(20), nullable=False, default="manual")
    status = Column(String(20), nullable=False, default="running")
    error_message = Column(Text, default="")
    started_at = Column(DateTime, default=beijing_now)
    finished_at = Column(DateTime, nullable=True)
    total_contents = Column(Integer, default=0)
    engagement_total = Column(JSON, default=dict)
    delta_from_prev = Column(JSON, default=dict)
    keyword = Column(String(255), default="", index=True)  # 搜索类任务的关键词

    creator = relationship("MonitoredCreator", back_populates="sessions")
    contents = relationship("ContentSnapshot", back_populates="session", cascade="all, delete-orphan")


class ContentSnapshot(Base):
    __tablename__ = "content_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("crawl_sessions.id"), nullable=False, index=True)
    content_id = Column(String(128), nullable=False, index=True)
    content_type = Column(String(20), default="video")
    title = Column(Text, default="")
    cover_url = Column(String(500), default="")
    create_time = Column(DateTime, nullable=True)
    duration_sec = Column(Float, default=0)
    url = Column(String(500), default="")
    tags = Column(JSON, default=list)
    music_title = Column(String(255), default="")
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    plays = Column(Integer, default=0)
    collects = Column(Integer, default=0)
    extra_data = Column(JSON, default=dict)

    session = relationship("CrawlSession", back_populates="contents")


class ScheduleConfig(Base):
    __tablename__ = "schedule_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    creator_id = Column(Integer, ForeignKey("monitored_creators.id"), unique=True, nullable=False)
    interval_minutes = Column(Integer, default=360)
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    extra_config = Column(JSON, default=dict)  # 扩展配置：keyword 等

    creator = relationship("MonitoredCreator", back_populates="schedule")


class DownloadStatus(str, enum.Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class DownloadTask(Base):
    """视频下载任务"""
    __tablename__ = "download_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_snapshot_id = Column(Integer, ForeignKey("content_snapshots.id"), nullable=False, index=True)
    platform = Column(String(20), nullable=False, index=True)
    content_id = Column(String(128), nullable=False)
    title = Column(Text, default="")
    status = Column(String(20), nullable=False, default="pending", index=True)
    progress = Column(Float, default=0)
    file_path = Column(String(500), default="")
    file_size = Column(Integer, default=0)
    error_message = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=beijing_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class PlatformCredential(Base):
    """平台登录凭证（Cookie），供爬虫携带请求"""
    __tablename__ = "platform_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, index=True)
    name = Column(String(255), default="")  # 备注名
    cookie_str = Column(Text, default="")    # 原始 Cookie 字符串
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)


class AnalysisStatus(str, enum.Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisTask(str, enum.Enum):
    """LLM 分析项 key，用于 VideoAnalysis.enabled_tasks。

    ASR/抽音频是所有 LLM 分析的共同前置，不在此枚举内——这里只描述
    "转写完成后跑哪些 LLM 总结"。
    """
    SUMMARY = "summary"        # 内容总结
    SCRIPT = "script"          # 脚本与制作手法
    KNOWLEDGE = "knowledge"    # 视频知识总结（知识点提炼）


# 默认启用的全部 LLM 分析项（创建任务时未指定 enabled_tasks 时使用）
ALL_ANALYSIS_TASKS = [t.value for t in AnalysisTask]


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
    knowledge_summary = Column(Text, default="")     # 视频知识总结-知识点提炼（markdown）
    enabled_tasks = Column(JSON, default=lambda: list(ALL_ANALYSIS_TASKS))  # 启用的 LLM 分析项 key 列表
    llm_model = Column(String(64), default="")
    error_message = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=beijing_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


# ---- 竞品调研 ----

class ResearchStatus(str, enum.Enum):
    PENDING = "pending"
    COLLECTING = "collecting"              # 抓取竞品主页 + 视频列表
    CRAWLING_COMMENTS = "crawling_comments"  # 爬评论区 + 截图
    ANALYZING = "analyzing"                # LLM 分析
    COMPLETED = "completed"
    FAILED = "failed"


class Competitor(Base):
    """竞品账号（抖音 sec_uid / B站 mid），账号分析模式的数据源。"""
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("platform", "platform_uid"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), default="")           # 用户自定义备注名
    platform = Column(String(20), nullable=False, index=True)
    platform_uid = Column(String(255), nullable=False)  # 抖音 sec_uid / B站 mid
    homepage_url = Column(String(500), default="")
    nickname = Column(String(255), default="")       # 平台昵称（首次调研后回填）
    note = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    follower_count = Column(Integer, default=0)
    last_researched_at = Column(DateTime, nullable=True)
    extra_info = Column(JSON, default=dict)
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)


class ResearchKeyword(Base):
    """调研关键词（赛道/品类），关键词调研模式的数据源。

    一个关键词 = 一个"竞品赛道"：调研时搜索该关键词下的头部视频
    与评论区，分析该赛道的内容格局与玩家。
    """
    __tablename__ = "research_keywords"
    __table_args__ = (UniqueConstraint("keyword", "platform"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(255), nullable=False)
    platform = Column(String(20), nullable=False, index=True)
    note = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    last_researched_at = Column(DateTime, nullable=True)
    extra_info = Column(JSON, default=dict)          # 最近一次调研的摘要（视频数/头部作者等）
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)


class ResearchTask(Base):
    """一次竞品调研任务：抓取 → 评论 → 截图 → LLM → Markdown 报告。

    mode=accounts（默认）：对 competitor_ids 做账号分析；
    mode=keyword：对 keyword_ids 做赛道分析（搜索关键词下的头部内容）。
    """
    __tablename__ = "research_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), default="")
    mode = Column(String(20), nullable=False, default="accounts", index=True)
    status = Column(String(30), nullable=False, default="pending", index=True)
    stage = Column(String(64), default="")           # 当前阶段中文文案
    progress = Column(Float, default=0)
    competitor_ids = Column(JSON, default=list)      # 参与调研的 competitor id 列表
    keyword_ids = Column(JSON, default=list)         # 参与调研的 keyword id 列表
    params = Column(JSON, default=dict)              # date_from/date_to/max_videos/...
    # 每竞品调研结果：[{competitor_id, name, nickname, platform, follower_count,
    # video_count, avg_likes, ..., content_analysis, comment_insight,
    # comments_crawled, screenshots, warnings}]
    competitor_stats = Column(JSON, default=list)
    market_overview = Column(Text, default="")       # 跨竞品 LLM 对比总结（markdown）
    markdown_path = Column(String(500), default="")  # 报告文件绝对路径
    trigger_type = Column(String(20), default="manual")
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=beijing_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ResearchComment(Base):
    """调研任务抓取的视频评论快照（主要来自爆款视频 Top N 评论区）。"""
    __tablename__ = "research_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    research_task_id = Column(Integer, ForeignKey("research_tasks.id"), nullable=False, index=True)
    competitor_id = Column(Integer, index=True, default=0)
    content_id = Column(String(128), default="", index=True)   # 平台内容 id（aweme_id/aid）
    comment_id = Column(String(128), default="")
    user_nickname = Column(String(255), default="")
    text = Column(Text, default="")
    likes = Column(Integer, default=0)
    create_time = Column(DateTime, nullable=True)
    ip_location = Column(String(64), default="")
    sub_comment_count = Column(Integer, default=0)
    extra = Column(JSON, default=dict)

    research_task = relationship("ResearchTask", back_populates="comments")


ResearchTask.comments = relationship(
    "ResearchComment", back_populates="research_task",
    cascade="all, delete-orphan", passive_deletes=True,
)


class ResearchDanmaku(Base):
    """调研任务抓取的B站原始弹幕快照（全量存库，供后续分析复用）。

    抖音无弹幕概念，仅 B站 视频会写入；关键词模式下 competitor_id 存 keyword id。
    """
    __tablename__ = "research_danmakus"

    id = Column(Integer, primary_key=True, autoincrement=True)
    research_task_id = Column(Integer, ForeignKey("research_tasks.id"), nullable=False, index=True)
    competitor_id = Column(Integer, index=True, default=0)
    content_id = Column(String(128), default="", index=True)   # B站 aid
    danmaku_id = Column(String(128), default="")
    text = Column(Text, default="")
    send_time = Column(DateTime, nullable=True)   # 弹幕发送时间
    dm_time = Column(Float, default=0)            # 视频内出现位置（秒）
    mode = Column(Integer, default=1)             # 1=滚动 4=底部 5=顶部
    font_size = Column(Integer, default=25)
    color = Column(String(8), default="")         # 十六进制颜色
    user_hash = Column(String(64), default="")    # 发送者 crc32 哈希（脱敏）
    pool = Column(Integer, default=0)
    extra = Column(JSON, default=dict)

    research_task = relationship("ResearchTask", back_populates="danmakus")


ResearchTask.danmakus = relationship(
    "ResearchDanmaku", back_populates="research_task",
    cascade="all, delete-orphan", passive_deletes=True,
)


class ResearchSchedule(Base):
    """竞品调研定时配置（单行全局）。"""
    __tablename__ = "research_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    interval_minutes = Column(Integer, default=10080)   # 默认一周
    enabled = Column(Boolean, default=False)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    params = Column(JSON, default=dict)                 # days/title_prefix/覆盖 enabled 竞品等
