"""FastAPI application entry point."""
import sys
import asyncio
import logging

# Windows 上 uvicorn 在 --reload / --workers>1 时会强制使用
# WindowsSelectorEventLoopPolicy（见 uvicorn asyncio_setup），而该 loop 不支持
# 子进程，会导致 Playwright 抓取报 NotImplementedError。这里设回
# ProactorEventLoop 作为兜底；但 uvicorn 可能先于 import 设置 Selector 策略，
# 因此 Playwright 抓取本身另由 hot_crawler.run_in_proactor_loop 在独立线程的
# Proactor loop 中执行，与 uvicorn 的 loop 解耦，确保可靠。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .api.creators import router as creators_router
from .api.platforms import router as platforms_router
from .api.sessions import router as sessions_router
from .api.contents import router as contents_router
from .api.schedules import router as schedules_router
from .api.ws import router as ws_router
from .api.credentials import router as credentials_router
from .api.hot import router as hot_router
from .config import settings
from .database import engine
from .models import Base

# ---- Logging ----
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 关掉 uvicorn.access 和 scrapling 的 200 日志轰炸
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
logging.getLogger("scrapling").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("fapai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .engine.scheduler import start_scheduler, stop_scheduler
    from .models import CrawlSession, SessionStatus
    from sqlalchemy import select, text as sa_text, update as sa_update

    logger.info("Starting FaPai Spider...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # ---- 轻量迁移：为已有数据库补加 extra_config 列 ----
        try:
            await conn.execute(
                sa_text("ALTER TABLE schedule_configs ADD COLUMN extra_config JSON DEFAULT '{}'")
            )
        except Exception:
            pass  # 列已存在

        # ---- 恢复机制：将上次中断的 running 任务标记为 interrupted ----
        result = await conn.execute(
            select(CrawlSession.id).where(
                CrawlSession.status == SessionStatus.RUNNING.value
            )
        )
        orphan_ids = result.scalars().all()
        if orphan_ids:
            from backend.models import beijing_now
            now = beijing_now()
            await conn.execute(
                sa_update(CrawlSession)
                .where(CrawlSession.id.in_(orphan_ids))
                .values(
                    status=SessionStatus.FAILED.value,
                    error_message="Server restarted — crawl interrupted",
                    finished_at=now,
                )
            )
            logger.warning(
                f"Recovered {len(orphan_ids)} interrupted session(s): {orphan_ids}"
            )
        else:
            logger.info("No interrupted sessions found")

    logger.info("Database tables ensured")
    start_scheduler()
    logger.info("Scheduler started")
    yield
    stop_scheduler()
    await engine.dispose()
    logger.info("FaPai Spider stopped")


app = FastAPI(title="FaPai Spider", version="0.1.0", lifespan=lifespan)

# ---- Request logging middleware (仅记录 4xx/5xx，忽略正常请求) ----
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    if response.status_code >= 400:
        logger.warning(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed:.2f}s)")
    return response

# ---- Global error handler ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from fastapi.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        # Let FastAPI handle HTTPExceptions normally, but log them
        logger.warning(
            f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )
    logger.error(
        f"Unhandled error on {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {exc}"},
    )


app.include_router(creators_router)
app.include_router(platforms_router)
app.include_router(sessions_router)
app.include_router(contents_router)
app.include_router(schedules_router)
app.include_router(ws_router)
app.include_router(credentials_router)
app.include_router(hot_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


static_dir = settings.static_dir

# API 路由之外的请求都走 SPA (StaticFiles 不 mount 到 "/" 避免覆盖 API)
if static_dir.exists() and list(static_dir.glob("index.html")):
    # 静态资源 (js/css/assets) 用 StaticFiles
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="static_assets")
    favicon = static_dir / "favicon.ico"
    if favicon.exists():
        app.mount("/favicon.ico", StaticFiles(directory=str(static_dir)), name="static_favicon")
    if (static_dir / "icons.svg").exists():
        app.mount("/icons.svg", StaticFiles(directory=str(static_dir)), name="static_icons")

    # SPA 兜底: 所有非 API 路由返回 index.html
    from fastapi.responses import FileResponse
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # 排除 API 和 WebSocket
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not found"}, status_code=404)
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path, media_type="text/html")
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Frontend not built — run `cd frontend && npm run build`"}, status_code=404)

    @app.get("/")
    async def root():
        return FileResponse(static_dir / "index.html", media_type="text/html")

else:
    @app.get("/")
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/docs")
