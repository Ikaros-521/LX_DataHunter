"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .api.creators import router as creators_router
from .api.platforms import router as platforms_router
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

app.include_router(creators_router)
app.include_router(platforms_router)

# Will mount static files in later tasks


@app.get("/api/health")
async def health():
    return {"status": "ok"}
