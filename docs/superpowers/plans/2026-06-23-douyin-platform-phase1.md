# 多平台创作者爬虫分析平台 — Phase 1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建可用的多平台爬虫分析平台 — 后端 FastAPI + SQLite，前端 React/Ant Design，Phase 1 实现抖音爬取 + 手动触发 + 结果检索展示。

**Architecture:** FastAPI 托管 React SPA，SQLite 单文件数据库，爬虫引擎策略模式（Phase 1 仅抖音适配器），APScheduler 进程内定时调度，WebSocket 推送爬取进度。

**Tech Stack:** Python 3.10, FastAPI 0.109, SQLAlchemy 2.x + aiosqlite, APScheduler, Pydantic 2.x, React 18 + Vite + TypeScript, Ant Design 5, Recharts, TanStack React Query

---

## 文件清单

```
backend/
├── main.py                    # FastAPI app, mount static, lifespan
├── config.py                  # Settings (Pydantic BaseSettings)
├── database.py                # AsyncEngine + session factory
├── models.py                  # SQLAlchemy ORM (4 tables)
├── schemas.py                 # Pydantic request/response schemas
├── api/
│   ├── __init__.py
│   ├── creators.py            # CRUD + trigger crawl
│   ├── platforms.py           # List supported platforms
│   ├── sessions.py            # Session list/detail
│   ├── contents.py            # Content search/retrieval
│   ├── schedules.py           # Schedule CRUD
│   └── ws.py                  # WebSocket progress
├── engine/
│   ├── __init__.py
│   ├── crawler.py             # Orchestrator: session + adapter + DB write + WS
│   ├── scheduler.py           # APScheduler lifecycle
│   └── platforms/
│       ├── __init__.py        # Adapter registry
│       ├── base.py            # Abstract base + shared types
│       └── douyin.py          # Douyin adapter (reuses demo code)
└── static/                    # (populated by npm run build)

frontend/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── vite-env.d.ts
    ├── api/
    │   ├── client.ts          # Axios instance + base config
    │   └── queries.ts         # React Query hooks
    ├── pages/
    │   ├── Dashboard.tsx
    │   ├── CreatorList.tsx
    │   ├── CreatorDetail.tsx
    │   ├── CrawlerConsole.tsx
    │   └── ContentSearch.tsx
    ├── components/
    │   ├── AppLayout.tsx      # Ant Design Layout + Sider
    │   ├── PlatformTag.tsx    # Colored platform badge
    │   ├── StatCard.tsx
    │   ├── ContentTable.tsx
    │   ├── TrendChart.tsx
    │   └── CrawlProgress.tsx
    └── hooks/
        ├── useCrawlProgress.ts
        └── useApi.ts
```

---

## Task 1: Backend 脚手架 — config + database + main

**Files:** Create `backend/config.py`, `backend/database.py`, `backend/main.py`

- [ ] **Step 1: Create `backend/config.py`**

```python
"""Application configuration via Pydantic BaseSettings."""
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = f"sqlite+aiosqlite:///{Path(__file__).parent.parent / 'data.db'}"
    static_dir: Path = Path(__file__).parent / "static"
    crawler_headless: bool = True
    chrome_executable: str | None = None
    log_level: str = "INFO"

    model_config = {"env_prefix": "FAPAI_", "env_file": ".env"}

settings = Settings()
```

- [ ] **Step 2: Create `backend/database.py`**

```python
"""Async SQLAlchemy engine and session factory."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 3: Create `backend/main.py`**

```python
"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .config import settings
from .database import engine
from .models import Base

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()

app = FastAPI(title="FaPai Spider", version="0.1.0", lifespan=lifespan)

# Will mount API routers in later tasks
# Will mount static files in later tasks

@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Install dependencies and verify**

```bash
cd backend && pip install fastapi uvicorn sqlalchemy aiosqlite pydantic-settings
python -c "from backend.main import app; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/database.py backend/main.py
git commit -m "feat: add backend scaffolding (config, database, main)"
```

---

## Task 2: SQLAlchemy Models

**Files:** Create `backend/models.py`

- [ ] **Step 1: Create `backend/models.py`**

```python
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
```

- [ ] **Step 2: Verify models create tables**

```bash
python -c "from backend.database import engine; from backend.models import Base; import asyncio; asyncio.run(Base.metadata.create_all(bind=engine.sync_engine if hasattr(engine,'sync_engine') else None))"
# Or just verify import:
python -c "from backend.models import MonitoredCreator, CrawlSession, ContentSnapshot, ScheduleConfig; print('Models OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat: add SQLAlchemy models (4 tables)"
```

---

## Task 3: Pydantic Schemas

**Files:** Create `backend/schemas.py`

- [ ] **Step 1: Create `backend/schemas.py`**

```python
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

class ScheduleResponse(BaseModel):
    id: int
    creator_id: int
    interval_minutes: int
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

# ---- Platform ----
class PlatformInfo(BaseModel):
    id: str
    name: str
    icon: str = ""
    status: str = "active"  # active / coming_soon

# ---- Trigger ----
class TriggerCrawlResponse(BaseModel):
    session_id: int
    message: str

# ---- Common ----
class MessageResponse(BaseModel):
    message: str
```

- [ ] **Step 2: Commit**

```bash
git add backend/schemas.py
git commit -m "feat: add Pydantic schemas"
```

---

## Task 4: Platform Adapter Base + Douyin Adapter

**Files:** Create `backend/engine/__init__.py`, `backend/engine/platforms/__init__.py`, `backend/engine/platforms/base.py`, `backend/engine/platforms/douyin.py`

- [ ] **Step 1: Create `backend/engine/__init__.py`** (empty)

- [ ] **Step 2: Create `backend/engine/platforms/__init__.py`**

```python
"""Platform adapter registry."""
from .base import BasePlatformAdapter, ContentItem, CreatorInfo
from .douyin import DouyinAdapter
from ..models import Platform

ADAPTERS: dict[Platform, BasePlatformAdapter] = {
    Platform.DOUYIN: DouyinAdapter(),
}

def get_adapter(platform: Platform) -> BasePlatformAdapter:
    adapter = ADAPTERS.get(platform)
    if not adapter:
        raise ValueError(f"Unsupported platform: {platform}")
    return adapter
```

- [ ] **Step 3: Create `backend/engine/platforms/base.py`**

```python
"""Abstract base for platform adapters and shared types."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

from backend.models import Platform, ContentType

ProgressCallback = Callable[[int, int, str], Coroutine[Any, Any, None]]

@dataclass
class CreatorInfo:
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    extra_info: dict = field(default_factory=dict)

@dataclass
class ContentItem:
    content_id: str
    content_type: ContentType = ContentType.VIDEO
    title: str = ""
    cover_url: str = ""
    create_time: datetime | None = None
    duration_sec: float = 0
    url: str = ""
    tags: list[str] = field(default_factory=list)
    music_title: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0
    plays: int = 0
    collects: int = 0
    extra_data: dict = field(default_factory=dict)

class BasePlatformAdapter(ABC):
    platform: Platform

    @abstractmethod
    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int = 50,
        headless: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[CreatorInfo, list[ContentItem]]:
        """Crawl a creator's content. Returns (creator_info, content_list)."""
        ...
```

- [ ] **Step 4: Create `backend/engine/platforms/douyin.py`** — wraps existing demo logic

```python
"""Douyin platform adapter — wraps Playwright-based crawler."""
import asyncio
import os
import json
from datetime import datetime
from typing import Any, Optional

from backend.models import Platform, ContentType
from .base import (
    BasePlatformAdapter, ContentItem, CreatorInfo, ProgressCallback,
)

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]

class DouyinAdapter(BasePlatformAdapter):
    platform = Platform.DOUYIN

    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int = 50,
        headless: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[CreatorInfo, list[ContentItem]]:
        from playwright.async_api import async_playwright

        user_url = f"https://www.douyin.com/user/{platform_uid}"
        xhr_data: list[dict] = []
        creator_info = CreatorInfo()

        chrome_exe = next((p for p in CHROME_PATHS if os.path.exists(p)), None)

        async with async_playwright() as p:
            launch_args = {
                "headless": headless,
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            }
            if chrome_exe:
                launch_args["executable_path"] = chrome_exe

            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                locale="zh-CN",
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            async def on_response(response):
                if "/aweme/v1/web/aweme/post" in response.url:
                    try:
                        xhr_data.append(await response.json())
                        total = sum(len(d.get("aweme_list", [])) for d in xhr_data)
                        if progress_callback:
                            await progress_callback(total, max_items, "running")
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                await page.goto(user_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await asyncio.sleep(5)

            # Check login wall
            if "login" in page.url or "passport" in page.url:
                if headless:
                    raise RuntimeError("Login required. Set headless=False to scan QR code.")
                print("[Douyin] Please log in via QR code in the browser window, then press Enter...")
                # In a real app, this would signal the frontend. For now, console input.
                await asyncio.get_event_loop().run_in_executor(None, input)
                await page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

            # Scroll for more
            prev_total = 0
            no_new = 0
            for _ in range(20):
                current_total = sum(len(d.get("aweme_list", [])) for d in xhr_data)
                if max_items > 0 and current_total >= max_items:
                    break
                if current_total == prev_total:
                    no_new += 1
                    if no_new >= 5:
                        break
                else:
                    no_new = 0
                prev_total = current_total
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

            await browser.close()

        # Extract creator info from first aweme
        items = self._parse_xhr_data(xhr_data, max_items)
        for d in xhr_data:
            for aweme in d.get("aweme_list", []):
                author = aweme.get("author", {})
                if author.get("nickname"):
                    creator_info = CreatorInfo(
                        nickname=author.get("nickname", ""),
                        signature=author.get("signature", ""),
                        avatar_url=(author.get("avatar_thumb", {}) or {}).get("url_list", [""])[0],
                        follower_count=author.get("follower_count", 0),
                        extra_info={"uid": author.get("uid", ""), "sec_uid": author.get("sec_uid", "")},
                    )
                break
            if creator_info.nickname:
                break

        return creator_info, items

    def _parse_xhr_data(self, xhr_data: list[dict], max_items: int) -> list[ContentItem]:
        items: list[ContentItem] = []
        seen_ids = set()
        for data in xhr_data:
            for aweme in data.get("aweme_list", []):
                aid = aweme.get("aweme_id", "")
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)

                stats = aweme.get("statistics", {})
                video = aweme.get("video", {})
                music = aweme.get("music", {})

                tags = [
                    t.get("hashtag_name", "")
                    for t in aweme.get("text_extra", [])
                    if t.get("hashtag_name")
                ]

                ct = aweme.get("create_time", 0)
                create_dt = datetime.fromtimestamp(ct) if ct else None

                cover = ""
                if video.get("cover"):
                    cover = video["cover"].get("url_list", [""])[0]

                images = []
                for img in (aweme.get("image_infos") or {}).values():
                    urls = img.get("url_list", [])
                    if urls:
                        images.append(urls[0])

                items.append(ContentItem(
                    content_id=aid,
                    content_type=ContentType.IMAGE if images else ContentType.VIDEO,
                    title=aweme.get("desc", ""),
                    cover_url=cover,
                    create_time=create_dt,
                    duration_sec=round(video.get("duration", 0) / 1000, 1),
                    url=f"https://www.douyin.com/video/{aid}" if aid else "",
                    tags=tags,
                    music_title=music.get("title", ""),
                    likes=stats.get("digg_count", 0),
                    comments=stats.get("comment_count", 0),
                    shares=stats.get("share_count", 0),
                    plays=stats.get("play_count", 0),
                    collects=stats.get("collect_count", 0),
                    extra_data={"images": images},
                ))

                if max_items > 0 and len(items) >= max_items:
                    return items
        return items
```

- [ ] **Step 5: Commit**

```bash
git add backend/engine/
git commit -m "feat: add platform adapter base + Douyin adapter"
```

---

## Task 5: Crawler Engine Orchestrator

**Files:** Create `backend/engine/crawler.py`

- [ ] **Step 1: Create `backend/engine/crawler.py`**

```python
"""Crawler orchestrator: ties together adapter, DB, and WebSocket."""
from datetime import datetime
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    MonitoredCreator, CrawlSession, ContentSnapshot, SessionStatus, TriggerType,
)
from .platforms import get_adapter, ContentItem, CreatorInfo

async def crawl_creator(
    creator: MonitoredCreator,
    db: AsyncSession,
    trigger_type: TriggerType = TriggerType.MANUAL,
    max_items: int = 50,
    headless: bool = True,
    progress_callback: Any = None,
) -> CrawlSession:
    """Execute a crawl for a creator. Creates session, writes results to DB."""
    adapter = get_adapter(creator.platform)

    # 1. Create session
    session = CrawlSession(
        creator_id=creator.id,
        platform=creator.platform.value,
        trigger_type=trigger_type.value,
        status=SessionStatus.RUNNING.value,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    # 2. Run crawler
    try:
        creator_info, items = await adapter.crawl_creator(
            platform_uid=creator.platform_uid,
            max_items=max_items,
            headless=headless,
            progress_callback=progress_callback,
        )
    except Exception as e:
        session.status = SessionStatus.FAILED.value
        session.error_message = str(e)[:500]
        session.finished_at = datetime.utcnow()
        await db.commit()
        raise

    # 3. Update creator info
    if creator_info.nickname:
        creator.nickname = creator_info.nickname
    if creator_info.signature:
        creator.signature = creator_info.signature
    if creator_info.avatar_url:
        creator.avatar_url = creator_info.avatar_url
    if creator_info.follower_count:
        creator.follower_count = creator_info.follower_count
    if creator_info.extra_info:
        creator.extra_info = creator_info.extra_info
    creator.updated_at = datetime.utcnow()

    # 4. Write content snapshots
    for item in items:
        snap = ContentSnapshot(
            session_id=session.id,
            content_id=item.content_id,
            content_type=item.content_type.value,
            title=item.title,
            cover_url=item.cover_url,
            create_time=item.create_time,
            duration_sec=item.duration_sec,
            url=item.url,
            tags=item.tags,
            music_title=item.music_title,
            likes=item.likes,
            comments=item.comments,
            shares=item.shares,
            plays=item.plays,
            collects=item.collects,
            extra_data=item.extra_data,
        )
        db.add(snap)

    # 5. Compute engagement and delta
    engagement = _sum_engagement(items)

    # Find previous completed session for delta
    from sqlalchemy import select, desc
    prev_stmt = (
        select(CrawlSession)
        .where(
            CrawlSession.creator_id == creator.id,
            CrawlSession.status == SessionStatus.COMPLETED.value,
            CrawlSession.id != session.id,
        )
        .order_by(desc(CrawlSession.finished_at))
        .limit(1)
    )
    prev_result = await db.execute(prev_stmt)
    prev_session = prev_result.scalar_one_or_none()

    delta = {}
    if prev_session:
        prev_eng = prev_session.engagement_total or {}
        delta = {
            "new_contents": max(0, len(items) - prev_session.total_contents),
            "removed_contents": max(0, prev_session.total_contents - len(items)),
            "likes_delta": engagement.get("likes", 0) - prev_eng.get("likes", 0),
            "comments_delta": engagement.get("comments", 0) - prev_eng.get("comments", 0),
            "shares_delta": engagement.get("shares", 0) - prev_eng.get("shares", 0),
            "plays_delta": engagement.get("plays", 0) - prev_eng.get("plays", 0),
            "collects_delta": engagement.get("collects", 0) - prev_eng.get("collects", 0),
        }

    # 6. Finalize session
    session.status = SessionStatus.COMPLETED.value
    session.finished_at = datetime.utcnow()
    session.total_contents = len(items)
    session.engagement_total = engagement
    session.delta_from_prev = delta

    await db.commit()
    await db.refresh(session)

    return session

def _sum_engagement(items: list[ContentItem]) -> dict:
    return {
        "likes": sum(i.likes for i in items),
        "comments": sum(i.comments for i in items),
        "shares": sum(i.shares for i in items),
        "plays": sum(i.plays for i in items),
        "collects": sum(i.collects for i in items),
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/engine/crawler.py
git commit -m "feat: add crawler engine orchestrator"
```

---

## Task 6: API — Platforms + Creators

**Files:** Create `backend/api/__init__.py`, `backend/api/platforms.py`, `backend/api/creators.py`

- [ ] **Step 1: Create `backend/api/__init__.py`** (empty)

- [ ] **Step 2: Create `backend/api/platforms.py`**

```python
"""Platform info endpoint."""
from fastapi import APIRouter
from backend.schemas import PlatformInfo

router = APIRouter(prefix="/api/v1/platforms", tags=["platforms"])

SUPPORTED = [
    PlatformInfo(id="douyin", name="抖音", icon="douyin", status="active"),
    PlatformInfo(id="bilibili", name="B站", icon="bilibili", status="coming_soon"),
    PlatformInfo(id="kuaishou", name="快手", icon="kuaishou", status="coming_soon"),
    PlatformInfo(id="xiaohongshu", name="小红书", icon="xiaohongshu", status="coming_soon"),
]

@router.get("")
async def list_platforms() -> list[PlatformInfo]:
    return SUPPORTED
```

- [ ] **Step 3: Create `backend/api/creators.py`**

```python
"""Creator CRUD + trigger crawl."""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from backend.database import get_db
from backend.models import MonitoredCreator, CrawlSession, SessionStatus, TriggerType
from backend.schemas import (
    CreatorCreate, CreatorResponse, CreatorListResponse, TriggerCrawlResponse,
)
from backend.engine.crawler import crawl_creator

router = APIRouter(prefix="/api/v1/creators", tags=["creators"])

async def _to_response(creator: MonitoredCreator, db: AsyncSession) -> dict:
    """Build CreatorResponse with latest stats."""
    latest_stmt = (
        select(CrawlSession)
        .where(
            CrawlSession.creator_id == creator.id,
            CrawlSession.status == SessionStatus.COMPLETED.value,
        )
        .order_by(desc(CrawlSession.finished_at))
        .limit(1)
    )
    result = await db.execute(latest_stmt)
    latest = result.scalar_one_or_none()

    data = {
        "id": creator.id,
        "platform": creator.platform,
        "platform_uid": creator.platform_uid,
        "nickname": creator.nickname,
        "signature": creator.signature,
        "avatar_url": creator.avatar_url,
        "follower_count": creator.follower_count,
        "extra_info": creator.extra_info,
        "created_at": creator.created_at,
        "updated_at": creator.updated_at,
        "latest_stats": None,
        "schedule": None,
    }
    if latest:
        data["latest_stats"] = {
            "total_contents": latest.total_contents,
            "engagement": latest.engagement,
            "fetched_at": latest.finished_at,
        }
    if creator.schedule:
        data["schedule"] = {
            "id": creator.schedule.id,
            "creator_id": creator.schedule.creator_id,
            "interval_minutes": creator.schedule.interval_minutes,
            "enabled": creator.schedule.enabled,
            "last_run_at": creator.schedule.last_run_at,
            "next_run_at": creator.schedule.next_run_at,
        }
    return data

@router.post("", response_model=CreatorResponse, status_code=201)
async def add_creator(body: CreatorCreate, db: AsyncSession = Depends(get_db)):
    # Check duplicate
    existing = await db.execute(
        select(MonitoredCreator).where(
            MonitoredCreator.platform == body.platform.value,
            MonitoredCreator.platform_uid == body.platform_uid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Creator already exists")

    creator = MonitoredCreator(
        platform=body.platform.value,
        platform_uid=body.platform_uid,
    )
    db.add(creator)
    await db.commit()
    await db.refresh(creator)
    return await _to_response(creator, db)

@router.get("", response_model=CreatorListResponse)
async def list_creators(
    platform: str | None = None,
    search: str = "",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MonitoredCreator)
    count_stmt = select(func.count(MonitoredCreator.id))

    if platform:
        stmt = stmt.where(MonitoredCreator.platform == platform)
        count_stmt = count_stmt.where(MonitoredCreator.platform == platform)
    if search:
        stmt = stmt.where(MonitoredCreator.nickname.ilike(f"%{search}%"))
        count_stmt = count_stmt.where(MonitoredCreator.nickname.ilike(f"%{search}%"))

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(desc(MonitoredCreator.updated_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = [await _to_response(c, db) for c in rows]
    return {"total": total, "items": items}

@router.get("/{creator_id}", response_model=CreatorResponse)
async def get_creator(creator_id: int, db: AsyncSession = Depends(get_db)):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")
    return await _to_response(creator, db)

@router.delete("/{creator_id}")
async def delete_creator(creator_id: int, db: AsyncSession = Depends(get_db)):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")
    await db.delete(creator)
    await db.commit()
    return {"message": "Deleted"}

@router.post("/{creator_id}/crawl", response_model=TriggerCrawlResponse, status_code=202)
async def trigger_crawl(
    creator_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")

    # Create session immediately
    session = CrawlSession(
        creator_id=creator.id,
        platform=creator.platform,
        trigger_type=TriggerType.MANUAL.value,
        status=SessionStatus.RUNNING.value,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    # Run crawl in background
    background_tasks.add_task(_run_crawl, creator, session.id)

    return {"session_id": session.id, "message": "Crawl started"}

async def _run_crawl(creator: MonitoredCreator, session_id: int):
    """Background task wrapper."""
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        # Reload within this session
        c = await db.get(MonitoredCreator, creator.id)
        session = await db.get(CrawlSession, session_id)
        if not c or not session:
            return
        try:
            await crawl_creator(
                creator=c,
                db=db,
                trigger_type=TriggerType.MANUAL,
            )
        except Exception as e:
            # Session was marked failed inside crawl_creator
            pass
```

- [ ] **Step 4: Register routers in `main.py`**

```python
# Add to main.py (after app = FastAPI(...))
from .api.creators import router as creators_router
from .api.platforms import router as platforms_router

app.include_router(creators_router)
app.include_router(platforms_router)
```

- [ ] **Step 5: Commit**

```bash
git add backend/api/__init__.py backend/api/platforms.py backend/api/creators.py
git commit -m "feat: add platforms and creators API endpoints"
```

---

## Task 7: API — Sessions + Contents + Schedules

**Files:** Create `backend/api/sessions.py`, `backend/api/contents.py`, `backend/api/schedules.py`

- [ ] **Step 1: Create `backend/api/sessions.py`**

```python
"""Crawl session endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from backend.database import get_db
from backend.models import CrawlSession, MonitoredCreator
from backend.schemas import SessionResponse, SessionListResponse

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

@router.get("", response_model=SessionListResponse)
async def list_sessions(
    creator_id: int | None = None,
    platform: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CrawlSession)
    count_stmt = select(func.count(CrawlSession.id))

    if creator_id:
        stmt = stmt.where(CrawlSession.creator_id == creator_id)
        count_stmt = count_stmt.where(CrawlSession.creator_id == creator_id)
    if platform:
        stmt = stmt.where(CrawlSession.platform == platform)
        count_stmt = count_stmt.where(CrawlSession.platform == platform)
    if status:
        stmt = stmt.where(CrawlSession.status == status)
        count_stmt = count_stmt.where(CrawlSession.status == status)

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(desc(CrawlSession.started_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for s in rows:
        creator = await db.get(MonitoredCreator, s.creator_id)
        items.append(SessionResponse(
            id=s.id,
            creator_id=s.creator_id,
            platform=s.platform,
            trigger_type=s.trigger_type,
            status=s.status,
            error_message=s.error_message,
            started_at=s.started_at,
            finished_at=s.finished_at,
            total_contents=s.total_contents,
            engagement_total=s.engagement_total,
            delta_from_prev=s.delta_from_prev,
            creator_nickname=creator.nickname if creator else "",
        ))
    return {"total": total, "items": items}

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(CrawlSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    creator = await db.get(MonitoredCreator, s.creator_id)
    return SessionResponse(
        id=s.id,
        creator_id=s.creator_id,
        platform=s.platform,
        trigger_type=s.trigger_type,
        status=s.status,
        error_message=s.error_message,
        started_at=s.started_at,
        finished_at=s.finished_at,
        total_contents=s.total_contents,
        engagement_total=s.engagement_total,
        delta_from_prev=s.delta_from_prev,
        creator_nickname=creator.nickname if creator else "",
    )

@router.get("/{session_id}/contents", response_model=dict)
async def get_session_contents(
    session_id: int,
    sort: str = "likes",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    from backend.models import ContentSnapshot
    from backend.schemas import ContentResponse, ContentListResponse

    allowed_sorts = {"likes", "comments", "shares", "plays", "collects", "create_time", "duration_sec"}
    if sort not in allowed_sorts:
        sort = "likes"

    sort_col = getattr(ContentSnapshot, sort)
    if order == "asc":
        sort_col = sort_col.asc()
    else:
        sort_col = sort_col.desc()

    count_stmt = select(func.count(ContentSnapshot.id)).where(ContentSnapshot.session_id == session_id)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(ContentSnapshot)
        .where(ContentSnapshot.session_id == session_id)
        .order_by(sort_col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        ContentResponse(
            id=r.id,
            session_id=r.session_id,
            content_id=r.content_id,
            content_type=r.content_type,
            title=r.title,
            cover_url=r.cover_url,
            create_time=r.create_time,
            duration_sec=r.duration_sec,
            url=r.url,
            tags=r.tags,
            music_title=r.music_title,
            likes=r.likes,
            comments=r.comments,
            shares=r.shares,
            plays=r.plays,
            collects=r.collects,
            extra_data=r.extra_data,
        )
        for r in rows
    ]
    return {"total": total, "items": [i.model_dump() for i in items]}
```

- [ ] **Step 2: Create `backend/api/contents.py`**

```python
"""Cross-creator content search."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from backend.database import get_db
from backend.models import ContentSnapshot, CrawlSession, MonitoredCreator, SessionStatus
from backend.schemas import ContentResponse, ContentListResponse

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

@router.get("", response_model=ContentListResponse)
async def search_contents(
    keyword: str = "",
    creator_id: int | None = None,
    platform: str | None = None,
    tag: str | None = None,
    content_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "likes",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ContentSnapshot)
        .join(CrawlSession, ContentSnapshot.session_id == CrawlSession.id)
        .where(CrawlSession.status == SessionStatus.COMPLETED.value)
    )
    count_stmt = (
        select(func.count(ContentSnapshot.id))
        .join(CrawlSession, ContentSnapshot.session_id == CrawlSession.id)
        .where(CrawlSession.status == SessionStatus.COMPLETED.value)
    )

    if keyword:
        stmt = stmt.where(ContentSnapshot.title.ilike(f"%{keyword}%"))
        count_stmt = count_stmt.where(ContentSnapshot.title.ilike(f"%{keyword}%"))
    if creator_id:
        stmt = stmt.where(CrawlSession.creator_id == creator_id)
        count_stmt = count_stmt.where(CrawlSession.creator_id == creator_id)
    if platform:
        stmt = stmt.where(CrawlSession.platform == platform)
        count_stmt = count_stmt.where(CrawlSession.platform == platform)
    if content_type:
        stmt = stmt.where(ContentSnapshot.content_type == content_type)
        count_stmt = count_stmt.where(ContentSnapshot.content_type == content_type)
    if tag:
        stmt = stmt.where(ContentSnapshot.tags.ilike(f"%{tag}%"))
        count_stmt = count_stmt.where(ContentSnapshot.tags.ilike(f"%{tag}%"))
    if date_from:
        stmt = stmt.where(ContentSnapshot.create_time >= date_from)
        count_stmt = count_stmt.where(ContentSnapshot.create_time >= date_from)
    if date_to:
        stmt = stmt.where(ContentSnapshot.create_time <= date_to)
        count_stmt = count_stmt.where(ContentSnapshot.create_time <= date_to)

    total = (await db.execute(count_stmt)).scalar() or 0

    allowed_sorts = {"likes", "comments", "shares", "plays", "collects", "create_time"}
    if sort not in allowed_sorts:
        sort = "likes"
    sort_col = getattr(ContentSnapshot, sort)
    sort_col = sort_col.desc() if order == "desc" else sort_col.asc()

    stmt = stmt.order_by(sort_col).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for r in rows:
        session = await db.get(CrawlSession, r.session_id)
        creator = await db.get(MonitoredCreator, session.creator_id) if session else None
        items.append(ContentResponse(
            id=r.id, session_id=r.session_id, content_id=r.content_id,
            content_type=r.content_type, title=r.title, cover_url=r.cover_url,
            create_time=r.create_time, duration_sec=r.duration_sec, url=r.url,
            tags=r.tags, music_title=r.music_title,
            likes=r.likes, comments=r.comments, shares=r.shares,
            plays=r.plays, collects=r.collects, extra_data=r.extra_data,
            platform=session.platform if session else "",
            creator_nickname=creator.nickname if creator else "",
        ))
    return {"total": total, "items": items}
```

- [ ] **Step 3: Create `backend/api/schedules.py`**

```python
"""Schedule configuration endpoints."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.models import ScheduleConfig, MonitoredCreator
from backend.schemas import ScheduleUpsert, ScheduleResponse

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ScheduleConfig))).scalars().all()
    return [ScheduleResponse(
        id=r.id, creator_id=r.creator_id,
        interval_minutes=r.interval_minutes, enabled=r.enabled,
        last_run_at=r.last_run_at, next_run_at=r.next_run_at,
    ) for r in rows]

@router.put("/{creator_id}", response_model=ScheduleResponse)
async def upsert_schedule(
    creator_id: int,
    body: ScheduleUpsert,
    db: AsyncSession = Depends(get_db),
):
    creator = await db.get(MonitoredCreator, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")

    existing = (await db.execute(
        select(ScheduleConfig).where(ScheduleConfig.creator_id == creator_id)
    )).scalar_one_or_none()

    if existing:
        existing.interval_minutes = body.interval_minutes
        existing.enabled = body.enabled
        existing.next_run_at = datetime.utcnow() + timedelta(minutes=body.interval_minutes)
        schedule = existing
    else:
        schedule = ScheduleConfig(
            creator_id=creator_id,
            interval_minutes=body.interval_minutes,
            enabled=body.enabled,
            next_run_at=datetime.utcnow() + timedelta(minutes=body.interval_minutes),
        )
        db.add(schedule)

    await db.commit()
    await db.refresh(schedule)
    return ScheduleResponse(
        id=schedule.id, creator_id=schedule.creator_id,
        interval_minutes=schedule.interval_minutes, enabled=schedule.enabled,
        last_run_at=schedule.last_run_at, next_run_at=schedule.next_run_at,
    )

@router.delete("/{creator_id}")
async def delete_schedule(creator_id: int, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(
        select(ScheduleConfig).where(ScheduleConfig.creator_id == creator_id)
    )).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()
    return {"message": "Deleted"}
```

- [ ] **Step 4: Register new routers in `main.py`**

```python
from .api.sessions import router as sessions_router
from .api.contents import router as contents_router
from .api.schedules import router as schedules_router

app.include_router(sessions_router)
app.include_router(contents_router)
app.include_router(schedules_router)
```

- [ ] **Step 5: Commit**

```bash
git add backend/api/sessions.py backend/api/contents.py backend/api/schedules.py backend/main.py
git commit -m "feat: add sessions, contents, schedules API endpoints"
```

---

## Task 8: WebSocket + APScheduler

**Files:** Create `backend/api/ws.py`, `backend/engine/scheduler.py`

- [ ] **Step 1: Create `backend/api/ws.py`**

```python
"""WebSocket endpoint for crawl progress."""
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# In-memory registry: session_id -> list of connected websockets
_active_connections: dict[int, list[WebSocket]] = {}

async def broadcast_progress(session_id: int, current: int, total: int, status: str):
    """Push progress to all clients watching this session."""
    payload = json.dumps({"type": "progress", "current": current, "total": total, "status": status})
    connections = _active_connections.get(session_id, [])
    dead = []
    for ws in connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.remove(ws)

@router.websocket("/ws/crawl/{session_id}")
async def crawl_progress(websocket: WebSocket, session_id: int):
    await websocket.accept()
    _active_connections.setdefault(session_id, []).append(websocket)
    try:
        while True:
            # Keep connection alive, client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _active_connections.get(session_id, []).remove(websocket)
```

- [ ] **Step 2: Create `backend/engine/scheduler.py`**

```python
"""APScheduler lifecycle management."""
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import ScheduleConfig, MonitoredCreator, SessionStatus
from .crawler import crawl_creator

scheduler = AsyncIOScheduler()

async def run_scheduled_check():
    """Check for due schedules and execute them."""
    async with AsyncSessionLocal() as db:
        now = datetime.utcnow()
        result = await db.execute(
            select(ScheduleConfig).where(
                ScheduleConfig.enabled == True,
                ScheduleConfig.next_run_at <= now,
            ).limit(5)
        )
        due = result.scalars().all()

        for schedule in due:
            creator = await db.get(MonitoredCreator, schedule.creator_id)
            if not creator:
                continue

            # Check if already running
            running_check = await db.execute(
                select(CrawlSession).where(
                    CrawlSession.creator_id == creator.id,
                    CrawlSession.status == SessionStatus.RUNNING.value,
                )
            )
            if running_check.scalar_one_or_none():
                continue

            from backend.models import TriggerType
            try:
                await crawl_creator(
                    creator=creator,
                    db=db,
                    trigger_type=TriggerType.SCHEDULED,
                )
            except Exception:
                pass
            finally:
                schedule.last_run_at = datetime.utcnow()
                from datetime import timedelta
                schedule.next_run_at = datetime.utcnow() + timedelta(minutes=schedule.interval_minutes)
                await db.commit()

def start_scheduler():
    scheduler.add_job(
        run_scheduled_check,
        trigger=IntervalTrigger(minutes=1),
        id="schedule_checker",
        name="Check due schedules every minute",
        replace_existing=True,
    )
    scheduler.start()

def stop_scheduler():
    scheduler.shutdown(wait=False)
```

- [ ] **Step 3: Wire into `main.py` lifespan**

```python
# Update lifespan in main.py:
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .engine.scheduler import start_scheduler, stop_scheduler
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    start_scheduler()
    yield
    stop_scheduler()
    await engine.dispose()
```

- [ ] **Step 4: Register WS router in `main.py`**

```python
from .api.ws import router as ws_router
app.include_router(ws_router)
```

- [ ] **Step 5: Commit**

```bash
git add backend/api/ws.py backend/engine/scheduler.py backend/main.py
git commit -m "feat: add WebSocket progress + APScheduler"
```

---

## Task 9: Backend Static Mount + Smoke Test

**Files:** Modify `backend/main.py`

- [ ] **Step 1: Add static file mount to `main.py`**

```python
# At the end of main.py (after all routers):
static_dir = settings.static_dir
if static_dir.exists() and list(static_dir.glob("index.html")):
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
else:
    # In dev mode, redirect root to API docs
    @app.get("/")
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/docs")
```

- [ ] **Step 2: Smoke test — start server and hit endpoints**

```bash
cd E:/Github_Pro/FaPai_Spider
python -m uvicorn backend.main:app --host 0.0.0.0 --port 28000 &

# Test health
curl http://localhost:28000/api/health

# Test platforms
curl http://localhost:28000/api/v1/platforms

# Test creators
curl -X POST http://localhost:28000/api/v1/creators \
  -H "Content-Type: application/json" \
  -d '{"platform":"douyin","platform_uid":"MS4wLjABAAAAuWZZlvIC2SHOWYQ4LkdxH1zcYBZWtVtARoh9wCpYsPYA3TeZ2b8g7p4hBQOEIj3y"}'
```

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: add static mount + finalize backend"
```

---

## Task 10: Frontend Scaffolding

**Files:** Create Vite + React + TypeScript project in `frontend/`

- [ ] **Step 1: Create Vite project**

```bash
cd E:/Github_Pro/FaPai_Spider
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install antd @ant-design/icons recharts @tanstack/react-query axios react-router-dom dayjs
npm install -D @types/react @types/react-dom
```

- [ ] **Step 2: Configure Vite proxy in `vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:28000',
      '/ws': { target: 'ws://localhost:28000', ws: true },
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
})
```

- [ ] **Step 3: Create `frontend/src/api/client.ts`**

```typescript
import axios from 'axios';

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

export default client;
```

- [ ] **Step 4: Create `frontend/src/api/queries.ts`**

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import client from './client';

// --- Types ---
export interface Platform {
  id: string; name: string; icon: string; status: string;
}

export interface Creator {
  id: number; platform: string; platform_uid: string;
  nickname: string; signature: string; avatar_url: string;
  follower_count: number; extra_info: Record<string, any>;
  created_at: string; updated_at: string;
  latest_stats: { total_contents: number; engagement: Record<string, number>; fetched_at: string } | null;
  schedule: Schedule | null;
}

export interface Session {
  id: number; creator_id: number; platform: string;
  trigger_type: string; status: string;
  error_message: string; started_at: string; finished_at: string | null;
  total_contents: number; engagement_total: Record<string, number>;
  delta_from_prev: Record<string, number>;
  creator_nickname: string;
}

export interface Content {
  id: number; session_id: number; content_id: string;
  content_type: string; title: string; cover_url: string;
  create_time: string | null; duration_sec: number; url: string;
  tags: string[]; music_title: string;
  likes: number; comments: number; shares: number; plays: number; collects: number;
  extra_data: Record<string, any>;
  platform: string; creator_nickname: string;
}

export interface Schedule {
  id: number; creator_id: number; interval_minutes: number;
  enabled: boolean; last_run_at: string | null; next_run_at: string | null;
}

export interface PaginatedResponse<T> {
  total: number; items: T[];
}

// --- Hooks ---
export function usePlatforms() {
  return useQuery<Platform[]>({ queryKey: ['platforms'], queryFn: () => client.get('/platforms').then(r => r.data) });
}

export function useCreators(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Creator>>({
    queryKey: ['creators', params],
    queryFn: () => client.get('/creators', { params }).then(r => r.data),
  });
}

export function useCreator(id: number) {
  return useQuery<Creator>({
    queryKey: ['creators', id],
    queryFn: () => client.get(`/creators/${id}`).then(r => r.data),
  });
}

export function useAddCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { platform: string; platform_uid: string }) => client.post('/creators', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['creators'] }),
  });
}

export function useDeleteCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/creators/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['creators'] }),
  });
}

export function useTriggerCrawl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (creatorId: number) => client.post(`/creators/${creatorId}/crawl`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  });
}

export function useSessions(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Session>>({
    queryKey: ['sessions', params],
    queryFn: () => client.get('/sessions', { params }).then(r => r.data),
  });
}

export function useSessionContents(sessionId: number, params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Content>>({
    queryKey: ['session-contents', sessionId, params],
    queryFn: () => client.get(`/sessions/${sessionId}/contents`, { params }).then(r => r.data),
    enabled: !!sessionId,
  });
}

export function useContents(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Content>>({
    queryKey: ['contents', params],
    queryFn: () => client.get('/contents', { params }).then(r => r.data),
  });
}

export function useSchedules() {
  return useQuery<Schedule[]>({ queryKey: ['schedules'], queryFn: () => client.get('/schedules').then(r => r.data) });
}

export function useUpsertSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ creatorId, data }: { creatorId: number; data: { interval_minutes: number; enabled: boolean } }) =>
      client.put(`/schedules/${creatorId}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
}
```

- [ ] **Step 5: Create `frontend/src/hooks/useCrawlProgress.ts`**

```typescript
import { useEffect, useState, useRef } from 'react';

interface ProgressEvent {
  type: 'progress';
  current: number;
  total: number;
  status: string;
}

export function useCrawlProgress(sessionId: number | null) {
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/crawl/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'progress') {
        setProgress(data);
      }
    };

    ws.onerror = () => console.warn('WebSocket error for session', sessionId);

    return () => {
      ws.close();
    };
  }, [sessionId]);

  return progress;
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold frontend with Vite + React + TS + Ant Design"
```

---

## Task 11: Frontend — App Shell + Dashboard

**Files:** Modify `frontend/src/App.tsx`, `frontend/src/main.tsx`; Create `frontend/src/components/AppLayout.tsx`, `frontend/src/components/StatCard.tsx`, `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Set up `main.tsx`**

```typescript
import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ConfigProvider locale={zhCN}>
          <App />
        </ConfigProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
```

- [ ] **Step 2: Create `AppLayout.tsx`**

```typescript
import { Layout, Menu, Typography } from 'antd';
import {
  DashboardOutlined, UserOutlined, SearchOutlined,
  SettingOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';

const { Sider, Content, Header } = Layout;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '概览' },
  { key: '/creators', icon: <UserOutlined />, label: '创作者管理' },
  { key: '/crawler', icon: <ThunderboltOutlined />, label: '爬虫控制台' },
  { key: '/search', icon: <SearchOutlined />, label: '内容检索' },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth="0">
        <div style={{ height: 32, margin: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Typography.Text style={{ color: '#fff', fontSize: 18, fontWeight: 'bold' }}>
            FaPai Spider
          </Typography.Text>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname === '/' ? '/' : '/' + location.pathname.split('/')[1]]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
          <Typography.Title level={4} style={{ margin: '16px 0' }}>
            {menuItems.find(m => {
              const seg = location.pathname.split('/')[1];
              return m.key === '/' + seg || (seg === '' && m.key === '/');
            })?.label || '概览'}
          </Typography.Title>
        </Header>
        <Content style={{ margin: 24, minHeight: 280 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
```

- [ ] **Step 3: Create `StatCard.tsx`**

```typescript
import { Card, Statistic } from 'antd';

interface StatCardProps {
  title: string;
  value: number | string;
  suffix?: string;
  icon?: React.ReactNode;
  color?: string;
  loading?: boolean;
}

export default function StatCard({ title, value, suffix, icon, color, loading }: StatCardProps) {
  return (
    <Card loading={loading}>
      <Statistic
        title={title}
        value={value}
        suffix={suffix}
        prefix={icon}
        valueStyle={color ? { color } : undefined}
      />
    </Card>
  );
}
```

- [ ] **Step 4: Create `Dashboard.tsx`**

```typescript
import { Row, Col, Table, Tag, List } from 'antd';
import { UserOutlined, VideoCameraOutlined, ThunderboltOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import StatCard from '../components/StatCard';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useSessions, useSchedules } from '../api/queries';
import dayjs from 'dayjs';

export default function Dashboard() {
  const navigate = useNavigate();
  const { data: creatorsData, isLoading: creatorsLoading } = useCreators();
  const { data: sessionsData, isLoading: sessionsLoading } = useSessions({ page_size: 10 });
  const { data: schedules } = useSchedules();

  const totalCreators = creatorsData?.total || 0;
  const totalSessions = sessionsData?.total || 0;
  const todaySessions = sessionsData?.items.filter(
    s => dayjs(s.started_at).isSame(dayjs(), 'day')
  ).length || 0;
  const activeSchedules = schedules?.filter(s => s.enabled).length || 0;

  const sessionColumns = [
    { title: '创作者', dataIndex: 'creator_nickname', key: 'creator' },
    {
      title: '平台', dataIndex: 'platform', key: 'platform',
      render: (p: string) => <PlatformTag platform={p} />,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (s: string) => (
        <Tag color={s === 'completed' ? 'green' : s === 'running' ? 'blue' : 'red'}>{s}</Tag>
      ),
    },
    { title: '内容数', dataIndex: 'total_contents', key: 'count' },
    {
      title: '时间', dataIndex: 'started_at', key: 'time',
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
  ];

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="监控创作者" value={totalCreators} icon={<UserOutlined />} color="#1677ff" loading={creatorsLoading} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="总爬取次数" value={totalSessions} icon={<ThunderboltOutlined />} color="#52c41a" loading={sessionsLoading} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="今日爬取" value={todaySessions} suffix="次" icon={<VideoCameraOutlined />} color="#fa8c16" />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="活跃定时任务" value={activeSchedules} icon={<ClockCircleOutlined />} color="#722ed1" />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={16}>
          <Table
            title={() => '最近爬取活动'}
            dataSource={sessionsData?.items || []}
            columns={sessionColumns}
            rowKey="id"
            loading={sessionsLoading}
            size="small"
            onRow={(record) => ({
              onClick: () => navigate(`/creators/${record.creator_id}`),
              style: { cursor: 'pointer' },
            })}
            pagination={false}
          />
        </Col>
        <Col xs={24} lg={8}>
          <List
            header="即将执行的定时任务"
            dataSource={schedules?.filter(s => s.enabled) || []}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={`Creator #${item.creator_id}`}
                  description={item.next_run_at ? `下次: ${dayjs(item.next_run_at).format('MM-DD HH:mm')}` : '未调度'}
                />
                <Tag>{item.interval_minutes}分钟/次</Tag>
              </List.Item>
            )}
          />
        </Col>
      </Row>
    </div>
  );
}
```

- [ ] **Step 5: Create `PlatformTag.tsx`**

```typescript
import { Tag } from 'antd';

const platformColors: Record<string, string> = {
  douyin: '#ff0050',
  bilibili: '#fb7299',
  kuaishou: '#ff4906',
  xiaohongshu: '#fe2c55',
};

const platformNames: Record<string, string> = {
  douyin: '抖音',
  bilibili: 'B站',
  kuaishou: '快手',
  xiaohongshu: '小红书',
};

export default function PlatformTag({ platform }: { platform: string }) {
  return (
    <Tag color={platformColors[platform] || '#999'}>
      {platformNames[platform] || platform}
    </Tag>
  );
}
```

- [ ] **Step 6: Update `App.tsx` with routes**

```typescript
import { Routes, Route } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import Dashboard from './pages/Dashboard';
import CreatorList from './pages/CreatorList';
import CreatorDetail from './pages/CreatorDetail';
import CrawlerConsole from './pages/CrawlerConsole';
import ContentSearch from './pages/ContentSearch';

export default function App() {
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/creators" element={<CreatorList />} />
        <Route path="/creators/:id" element={<CreatorDetail />} />
        <Route path="/crawler" element={<CrawlerConsole />} />
        <Route path="/search" element={<ContentSearch />} />
      </Routes>
    </AppLayout>
  );
}
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/
git commit -m "feat: add App shell, Dashboard, PlatformTag, StatCard"
```

---

## Task 12: Frontend — CreatorList + CreatorDetail

**Files:** Create `frontend/src/pages/CreatorList.tsx`, `frontend/src/pages/CreatorDetail.tsx`, `frontend/src/components/TrendChart.tsx`, `frontend/src/components/ContentTable.tsx`

- [ ] **Step 1: Create `CreatorList.tsx`**

```typescript
import { useState } from 'react';
import { Table, Button, Modal, Form, Select, Input, Space, Popconfirm, message, Tag } from 'antd';
import { PlusOutlined, ThunderboltOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useAddCreator, useDeleteCreator, useTriggerCrawl, usePlatforms } from '../api/queries';
import dayjs from 'dayjs';

export default function CreatorList() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [platformFilter, setPlatformFilter] = useState<string | undefined>();
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading } = useCreators({ page, page_size: 20, platform: platformFilter });
  const { data: platforms } = usePlatforms();
  const addMutation = useAddCreator();
  const deleteMutation = useDeleteCreator();
  const crawlMutation = useTriggerCrawl();

  const activePlatforms = platforms?.filter(p => p.status === 'active') || [];

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    {
      title: '平台', dataIndex: 'platform', key: 'platform', width: 100,
      render: (p: string) => <PlatformTag platform={p} />,
    },
    { title: '昵称', dataIndex: 'nickname', key: 'nickname', render: (n: string) => n || '(待爬取)' },
    { title: '粉丝', dataIndex: 'follower_count', key: 'follower', render: (n: number) => n ? n.toLocaleString() : '-' },
    {
      title: '最近爬取', key: 'latest',
      render: (_: any, record: any) => record.latest_stats
        ? dayjs(record.latest_stats.fetched_at).format('MM-DD HH:mm')
        : '未爬取',
    },
    {
      title: '定时', key: 'schedule',
      render: (_: any, record: any) => record.schedule?.enabled
        ? <Tag color="green">{record.schedule.interval_minutes}分钟</Tag>
        : <Tag>未设置</Tag>,
    },
    {
      title: '操作', key: 'actions', width: 250,
      render: (_: any, record: any) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/creators/${record.id}`)}>详情</Button>
          <Button size="small" icon={<ThunderboltOutlined />} loading={crawlMutation.isPending}
            onClick={() => crawlMutation.mutate(record.id, {
              onSuccess: (res: any) => message.success(`爬取已启动 (Session #${res.data.session_id})`),
            })}>爬取</Button>
          <Popconfirm title="确认删除?" onConfirm={() => deleteMutation.mutate(record.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      await addMutation.mutateAsync(values);
      message.success('创作者已添加');
      setAddModalOpen(false);
      form.resetFields();
    } catch (e) {
      // validation error
    }
  };

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="筛选平台"
          style={{ width: 120 }}
          onChange={(v) => setPlatformFilter(v)}
          options={(platforms || []).map(p => ({ label: p.name, value: p.id }))}
        />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddModalOpen(true)}>
          添加创作者
        </Button>
      </Space>

      <Table
        dataSource={data?.items || []}
        columns={columns}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: page,
          total: data?.total || 0,
          onChange: setPage,
        }}
      />

      <Modal
        title="添加创作者"
        open={addModalOpen}
        onOk={handleAdd}
        onCancel={() => setAddModalOpen(false)}
        confirmLoading={addMutation.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
            <Select options={activePlatforms.map(p => ({ label: p.name, value: p.id }))} />
          </Form.Item>
          <Form.Item name="platform_uid" label="平台UID" rules={[{ required: true }]}
            extra="抖音: 从分享链接 /user/ 后获取。B站: 用户空间URL中的数字ID"
          >
            <Input placeholder="输入平台用户唯一标识" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Create `TrendChart.tsx`**

```typescript
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Card } from 'antd';

interface TrendChartProps {
  data: Array<{
    time: string;
    likes: number;
    comments: number;
    shares: number;
    plays: number;
  }>;
  loading?: boolean;
}

export default function TrendChart({ data, loading }: TrendChartProps) {
  return (
    <Card title="互动趋势" loading={loading}>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="time" />
          <YAxis />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="likes" stroke="#1677ff" name="点赞" />
          <Line type="monotone" dataKey="comments" stroke="#52c41a" name="评论" />
          <Line type="monotone" dataKey="shares" stroke="#fa8c16" name="转发" />
          <Line type="monotone" dataKey="plays" stroke="#722ed1" name="播放" />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}
```

- [ ] **Step 3: Create `ContentTable.tsx`**

```typescript
import { Table, Tag, Image } from 'antd';
import type { Content } from '../api/queries';
import dayjs from 'dayjs';

interface ContentTableProps {
  data: Content[];
  loading?: boolean;
  total?: number;
  page?: number;
  onPageChange?: (page: number) => void;
  onRowClick?: (record: Content) => void;
}

export default function ContentTable({ data, loading, total, page, onPageChange, onRowClick }: ContentTableProps) {
  const columns = [
    {
      title: '封面', dataIndex: 'cover_url', key: 'cover', width: 80,
      render: (url: string) => url ? <Image src={url} width={60} height={80} style={{ objectFit: 'cover', borderRadius: 4 }} /> : <div style={{ width: 60, height: 80, background: '#f0f0f0', borderRadius: 4 }} />,
    },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true, render: (t: string) => t || '(无标题)' },
    {
      title: '类型', dataIndex: 'content_type', key: 'type', width: 60,
      render: (t: string) => <Tag>{t === 'video' ? '视频' : t === 'image' ? '图文' : t}</Tag>,
    },
    { title: '👍', dataIndex: 'likes', key: 'likes', width: 80, render: (n: number) => n?.toLocaleString() },
    { title: '💬', dataIndex: 'comments', key: 'comments', width: 80, render: (n: number) => n?.toLocaleString() },
    { title: '🔄', dataIndex: 'shares', key: 'shares', width: 80, render: (n: number) => n?.toLocaleString() },
    {
      title: '时间', dataIndex: 'create_time', key: 'create_time', width: 120,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD') : '-',
    },
  ];

  return (
    <Table
      dataSource={data}
      columns={columns}
      rowKey="id"
      loading={loading}
      size="small"
      pagination={total ? { current: page, total, onChange: onPageChange, pageSize: 20 } : false}
      onRow={(record) => ({
        onClick: () => onRowClick?.(record),
        style: { cursor: onRowClick ? 'pointer' : undefined },
      })}
    />
  );
}
```

- [ ] **Step 4: Create `CreatorDetail.tsx`**

```typescript
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Descriptions, Space, Button, Drawer, Tag, Row, Col, message } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import PlatformTag from '../components/PlatformTag';
import TrendChart from '../components/TrendChart';
import ContentTable from '../components/ContentTable';
import { useCreator, useSessions, useSessionContents, useTriggerCrawl } from '../api/queries';
import dayjs from 'dayjs';
import type { Content } from '../api/queries';

export default function CreatorDetail() {
  const { id } = useParams<{ id: string }>();
  const creatorId = parseInt(id!);
  const { data: creator, isLoading } = useCreator(creatorId);
  const { data: sessionsData } = useSessions({ creator_id: creatorId, page_size: 100 });
  const crawlMutation = useTriggerCrawl();

  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [contentPage, setContentPage] = useState(1);
  const { data: contentsData, isLoading: contentsLoading } = useSessionContents(
    selectedSessionId!,
    { page: contentPage, page_size: 20, sort: 'likes', order: 'desc' }
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedContent, setSelectedContent] = useState<Content | null>(null);

  const sessions = sessionsData?.items || [];

  // Build trend data from all completed sessions
  const trendData = sessions
    .filter(s => s.status === 'completed')
    .reverse()
    .map(s => ({
      time: dayjs(s.finished_at).format('MM-DD HH:mm'),
      likes: s.engagement_total?.likes || 0,
      comments: s.engagement_total?.comments || 0,
      shares: s.engagement_total?.shares || 0,
      plays: s.engagement_total?.plays || 0,
    }));

  return (
    <div>
      <Descriptions
        title={
          <Space>
            {creator?.nickname || '加载中...'}
            {creator?.platform && <PlatformTag platform={creator.platform} />}
          </Space>
        }
        bordered
        column={{ xs: 1, sm: 2 }}
        loading={isLoading}
        extra={
          <Button type="primary" icon={<ThunderboltOutlined />}
            loading={crawlMutation.isPending}
            onClick={() => crawlMutation.mutate(creatorId, {
              onSuccess: (res: any) => message.success(`爬取已启动 (Session #${res.data.session_id})`),
            })}>
            立即爬取
          </Button>
        }
      >
        <Descriptions.Item label="平台UID">{creator?.platform_uid}</Descriptions.Item>
        <Descriptions.Item label="粉丝">{creator?.follower_count?.toLocaleString()}</Descriptions.Item>
        <Descriptions.Item label="简介" span={2}>{creator?.signature || '-'}</Descriptions.Item>
        <Descriptions.Item label="加入时间">{creator?.created_at ? dayjs(creator.created_at).format('YYYY-MM-DD HH:mm') : '-'}</Descriptions.Item>
        <Descriptions.Item label="最近爬取">{creator?.latest_stats?.fetched_at ? dayjs(creator.latest_stats.fetched_at).format('YYYY-MM-DD HH:mm') : '未爬取'}</Descriptions.Item>
      </Descriptions>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col span={24}>
          <TrendChart data={trendData} loading={isLoading} />
        </Col>
      </Row>

      <div style={{ marginTop: 24 }}>
        <h3>爬取历史</h3>
        <Space wrap style={{ marginBottom: 16 }}>
          {sessions.map(s => (
            <Button
              key={s.id}
              type={selectedSessionId === s.id ? 'primary' : 'default'}
              size="small"
              onClick={() => { setSelectedSessionId(s.id); setContentPage(1); }}
            >
              {dayjs(s.finished_at || s.started_at).format('MM-DD HH:mm')}
              <Tag color={s.status === 'completed' ? 'green' : 'blue'} style={{ marginLeft: 8 }}>
                {s.total_contents}条
              </Tag>
            </Button>
          ))}
        </Space>

        {selectedSessionId && (
          <ContentTable
            data={contentsData?.items || []}
            loading={contentsLoading}
            total={contentsData?.total}
            page={contentPage}
            onPageChange={setContentPage}
            onRowClick={(content) => { setSelectedContent(content); setDrawerOpen(true); }}
          />
        )}
      </div>

      <Drawer
        title={selectedContent?.title?.slice(0, 50)}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
      >
        {selectedContent && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="点赞">{selectedContent.likes.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="评论">{selectedContent.comments.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="转发">{selectedContent.shares.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="播放">{selectedContent.plays.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="收藏">{selectedContent.collects.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="时长">{selectedContent.duration_sec}秒</Descriptions.Item>
            <Descriptions.Item label="发布时间">
              {selectedContent.create_time ? dayjs(selectedContent.create_time).format('YYYY-MM-DD HH:mm') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="标签">
              {selectedContent.tags?.map(t => <Tag key={t}>{t}</Tag>)}
            </Descriptions.Item>
            {selectedContent.music_title && (
              <Descriptions.Item label="音乐">{selectedContent.music_title}</Descriptions.Item>
            )}
            {selectedContent.url && (
              <Descriptions.Item label="链接">
                <a href={selectedContent.url} target="_blank" rel="noreferrer">打开</a>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CreatorList.tsx frontend/src/pages/CreatorDetail.tsx
git add frontend/src/components/TrendChart.tsx frontend/src/components/ContentTable.tsx
git commit -m "feat: add CreatorList and CreatorDetail pages"
```

---

## Task 13: Frontend — CrawlerConsole + ContentSearch

**Files:** Create `frontend/src/pages/CrawlerConsole.tsx`, `frontend/src/pages/ContentSearch.tsx`, `frontend/src/components/CrawlProgress.tsx`

- [ ] **Step 1: Create `CrawlProgress.tsx`**

```typescript
import { Progress, Card, Tag } from 'antd';

interface CrawlProgressProps {
  sessionId: number;
  current: number;
  total: number;
  status: string;
  creatorName: string;
}

export default function CrawlProgress({ sessionId, current, total, status, creatorName }: CrawlProgressProps) {
  const percent = total > 0 ? Math.round((current / total) * 100) : 0;
  const statusColor = status === 'completed' ? 'success' : status === 'failed' ? 'exception' : 'active';

  return (
    <Card size="small" title={`#${sessionId} - ${creatorName}`}
      extra={<Tag color={statusColor === 'success' ? 'green' : statusColor === 'exception' ? 'red' : 'blue'}>{status}</Tag>}>
      <Progress percent={percent} status={statusColor === 'active' ? 'active' : statusColor === 'success' ? 'success' : 'exception'} />
      <div style={{ marginTop: 8, color: '#888' }}>
        已捕获: {current} / {total > 0 ? total : '?'}
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: Create `CrawlerConsole.tsx`**

```typescript
import { useState } from 'react';
import { Row, Col, Table, Select, Button, Space, Tag, message } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import CrawlProgress from '../components/CrawlProgress';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useSessions, useTriggerCrawl } from '../api/queries';
import { useCrawlProgress } from '../hooks/useCrawlProgress';
import dayjs from 'dayjs';

export default function CrawlerConsole() {
  const [selectedCreator, setSelectedCreator] = useState<number | null>(null);
  const { data: creatorsData } = useCreators({ page_size: 200 });
  const { data: sessionsData, isLoading } = useSessions({ page_size: 20 });
  const crawlMutation = useTriggerCrawl();

  // Find running sessions
  const runningSessions = sessionsData?.items.filter(s => s.status === 'running') || [];
  const [watchingSession, setWatchingSession] = useState<number | null>(
    runningSessions[0]?.id || null
  );
  const progress = useCrawlProgress(watchingSession);

  const handleTrigger = () => {
    if (!selectedCreator) {
      message.warning('请选择要爬取的创作者');
      return;
    }
    crawlMutation.mutate(selectedCreator, {
      onSuccess: (res: any) => {
        message.success(`爬取已启动 (Session #${res.data.session_id})`);
        setTimeout(() => setWatchingSession(res.data.session_id), 500);
      },
    });
  };

  const sessionColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: '创作者', dataIndex: 'creator_nickname', key: 'creator' },
    { title: '平台', dataIndex: 'platform', key: 'platform', render: (p: string) => <PlatformTag platform={p} /> },
    { title: '触发', dataIndex: 'trigger_type', key: 'trigger', render: (t: string) => <Tag>{t === 'manual' ? '手动' : '定时'}</Tag> },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (s: string) => <Tag color={s === 'completed' ? 'green' : s === 'running' ? 'blue' : 'red'}>{s}</Tag>,
    },
    { title: '内容数', dataIndex: 'total_contents', key: 'count' },
    { title: '时间', dataIndex: 'started_at', key: 'time', render: (t: string) => dayjs(t).format('MM-DD HH:mm:ss') },
  ];

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Space>
            <Select
              showSearch
              placeholder="选择创作者"
              style={{ width: 250 }}
              value={selectedCreator}
              onChange={setSelectedCreator}
              filterOption={(input, option) => (option?.label as string || '').toLowerCase().includes(input.toLowerCase())}
              options={(creatorsData?.items || []).map(c => ({
                label: `${c.nickname || c.platform_uid} (${c.platform})`,
                value: c.id,
              }))}
            />
            <Button type="primary" icon={<ThunderboltOutlined />}
              loading={crawlMutation.isPending}
              onClick={handleTrigger}>
              立即爬取
            </Button>
          </Space>
        </Col>
      </Row>

      {runningSessions.length > 0 && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={24}>
            <h3>正在运行的任务</h3>
          </Col>
          {runningSessions.map(s => (
            <Col xs={24} sm={12} md={8} key={s.id}>
              <CrawlProgress
                sessionId={s.id}
                current={progress?.current || 0}
                total={progress?.total || s.total_contents}
                status={progress?.status || s.status}
                creatorName={s.creator_nickname}
              />
            </Col>
          ))}
        </Row>
      )}

      <Row style={{ marginTop: 24 }}>
        <Col span={24}>
          <Table
            title={() => '爬取历史'}
            dataSource={sessionsData?.items || []}
            columns={sessionColumns}
            rowKey="id"
            loading={isLoading}
            size="small"
            pagination={{ pageSize: 20 }}
          />
        </Col>
      </Row>
    </div>
  );
}
```

- [ ] **Step 3: Create `ContentSearch.tsx`**

```typescript
import { useState } from 'react';
import { Input, Select, DatePicker, Space, Button, message } from 'antd';
import { SearchOutlined, ExportOutlined } from '@ant-design/icons';
import PlatformTag from '../components/PlatformTag';
import ContentTable from '../components/ContentTable';
import { useContents, useCreators, usePlatforms } from '../api/queries';
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;

export default function ContentSearch() {
  const [keyword, setKeyword] = useState('');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [platform, setPlatform] = useState<string | undefined>();
  const [creatorId, setCreatorId] = useState<number | undefined>();
  const [contentType, setContentType] = useState<string | undefined>();
  const [sort, setSort] = useState('likes');
  const [dateRange, setDateRange] = useState<[string, string] | null>(null);
  const [page, setPage] = useState(1);

  const params: Record<string, any> = {
    page, page_size: 20, sort, order: 'desc',
  };
  if (searchKeyword) params.keyword = searchKeyword;
  if (platform) params.platform = platform;
  if (creatorId) params.creator_id = creatorId;
  if (contentType) params.content_type = contentType;
  if (dateRange) { params.date_from = dateRange[0]; params.date_to = dateRange[1]; }

  const { data, isLoading } = useContents(params);
  const { data: creatorsData } = useCreators({ page_size: 200 });
  const { data: platforms } = usePlatforms();

  const handleSearch = () => {
    setSearchKeyword(keyword);
    setPage(1);
  };

  const handleExport = () => {
    if (!data?.items?.length) {
      message.warning('没有数据可导出');
      return;
    }
    const json = JSON.stringify(data.items, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `export_${dayjs().format('YYYYMMDD_HHmmss')}.json`;
    a.click();
    URL.revokeObjectURL(url);
    message.success('导出成功');
  };

  return (
    <div>
      <Space wrap style={{ marginBottom: 16, width: '100%' }}>
        <Input.Search
          placeholder="搜索关键词"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onSearch={handleSearch}
          style={{ width: 250 }}
          enterButton={<SearchOutlined />}
        />
        <Select
          allowClear placeholder="平台" style={{ width: 100 }}
          value={platform} onChange={setPlatform}
          options={(platforms || []).map(p => ({ label: p.name, value: p.id }))}
        />
        <Select
          allowClear showSearch placeholder="创作者" style={{ width: 200 }}
          value={creatorId} onChange={setCreatorId}
          filterOption={(input, option) => (option?.label as string || '').includes(input)}
          options={(creatorsData?.items || []).map(c => ({
            label: `${c.nickname || c.platform_uid}`,
            value: c.id,
          }))}
        />
        <Select
          allowClear placeholder="类型" style={{ width: 100 }}
          value={contentType} onChange={setContentType}
          options={[
            { label: '视频', value: 'video' },
            { label: '图文', value: 'image' },
          ]}
        />
        <Select
          value={sort} onChange={setSort} style={{ width: 100 }}
          options={[
            { label: '按点赞', value: 'likes' },
            { label: '按评论', value: 'comments' },
            { label: '按转发', value: 'shares' },
            { label: '按播放', value: 'plays' },
            { label: '按时间', value: 'create_time' },
          ]}
        />
        <RangePicker
          onChange={(_, dateStrings) => {
            if (dateStrings[0] && dateStrings[1]) {
              setDateRange([dateStrings[0], dateStrings[1]]);
            } else {
              setDateRange(null);
            }
          }}
        />
        <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
      </Space>

      <ContentTable
        data={data?.items || []}
        loading={isLoading}
        total={data?.total || 0}
        page={page}
        onPageChange={setPage}
      />
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CrawlerConsole.tsx frontend/src/pages/ContentSearch.tsx frontend/src/components/CrawlProgress.tsx
git commit -m "feat: add CrawlerConsole and ContentSearch pages"
```

---

## Task 14: Integration — Build + Verify

**Files:** Modify `requirements.txt`, create `README.md`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi>=0.109.0
uvicorn[standard]>=0.34.0
sqlalchemy>=2.0
aiosqlite>=0.20.0
apscheduler>=3.10.0
pydantic>=2.0
pydantic-settings>=2.0
scrapling[fetchers]>=0.4.9
```

- [ ] **Step 2: Install all deps**

```bash
cd E:/Github_Pro/FaPai_Spider
pip install -r requirements.txt
```

- [ ] **Step 3: Build frontend**

```bash
cd frontend && npm run build
```

This places built files in `../backend/static/`.

- [ ] **Step 4: Start and smoke test**

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 28000
```

Open http://localhost:28000 — should see the React app.
API docs at http://localhost:28000/docs.

- [ ] **Step 5: Add a creator and trigger crawl**

```
POST /api/v1/creators {"platform":"douyin","platform_uid":"<sec_uid>"}
POST /api/v1/creators/1/crawl
GET /api/v1/sessions (verify session created)
GET /api/v1/contents (verify content appears)
```

- [ ] **Step 6: Create README.md** (optional, brief)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt README.md
git commit -m "feat: integration — build, deps, README"
```

---

## 完工检查清单

- [ ] `python -m uvicorn backend.main:app` 启动后 `/api/health` 返回 OK
- [ ] `/api/v1/platforms` 返回 4 个平台（抖音 active，其余 coming_soon）
- [ ] `POST /api/v1/creators` 添加抖音用户成功
- [ ] 重复添加同一用户返回 409
- [ ] `POST /api/v1/creators/1/crawl` 启动爬虫，返回 session_id
- [ ] 爬虫完成，`GET /api/v1/sessions` 能看到 completed session
- [ ] `GET /api/v1/contents?platform=douyin` 能看到爬取的内容
- [ ] 前端 `npm run dev` 后 http://localhost:3000 显示 Dashboard
- [ ] 前端能添加创作者、触发爬取、查看内容搜索
- [ ] `npm run build` 产物正确输出到 `backend/static/`
- [ ] 直接 `python -m uvicorn backend.main:app` 访问 `http://localhost:28000` 显示前端
