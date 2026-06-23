"""SQLAlchemy ORM models.  JSON 列使用 SQLAlchemy JSON 类型，自动序列化/反序列化。"""
import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, JSON,
    DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    total_contents = Column(Integer, default=0)
    engagement_total = Column(JSON, default=dict)
    delta_from_prev = Column(JSON, default=dict)

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

    creator = relationship("MonitoredCreator", back_populates="schedule")
