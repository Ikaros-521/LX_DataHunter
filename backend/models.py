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
