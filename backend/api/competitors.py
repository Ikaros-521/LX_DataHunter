"""竞品账号 CRUD + 关键词发现。"""
import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db, AsyncSessionLocal
from backend.models import Competitor
from backend.schemas import (
    CompetitorCreate, CompetitorUpdate, CompetitorResponse,
    CompetitorListResponse, DiscoverRequest, DiscoverResponse,
    DiscoveredCompetitor,
)
from backend.engine.competitor_discovery import discover_bilibili, discover_douyin
from backend.engine.hot_crawler import _run_coro_in_thread

logger = logging.getLogger("lx_datahunter.api.competitors")
router = APIRouter(prefix="/api/v1/competitors", tags=["competitors"])


def parse_platform_uid(platform: str, raw: str) -> tuple[str, str]:
    """解析用户输入的竞品标识 → (platform_uid, homepage_url)。

    支持主页链接或纯 uid：抖音 sec_uid（含 /user/{sec_uid} 链接）、
    B站 mid（含 space.bilibili.com/{mid} 链接）。
    """
    raw = (raw or "").strip()
    if platform == "douyin":
        m = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", raw)
        if m:
            uid = m.group(1)
        elif raw and " " not in raw and "/" not in raw and "http" not in raw.lower():
            uid = raw
        else:
            raise ValueError("无法识别的抖音标识：请粘贴主页链接（含 /user/xxx）或 sec_uid")
        return uid, f"https://www.douyin.com/user/{uid}"
    if platform == "bilibili":
        m = re.search(r"space\.bilibili\.com/(\d+)", raw)
        if m:
            uid = m.group(1)
        elif raw.isdigit():
            uid = raw
        else:
            raise ValueError("无法识别的B站标识：请粘贴空间链接（space.bilibili.com/xxx）或数字 mid")
        return uid, f"https://space.bilibili.com/{uid}"
    raise ValueError(f"暂不支持平台：{platform}")


@router.get("", response_model=CompetitorListResponse)
async def list_competitors(
    platform: str | None = None,
    search: str = "",
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Competitor)
    count_stmt = select(func.count(Competitor.id))
    if platform:
        stmt = stmt.where(Competitor.platform == platform)
        count_stmt = count_stmt.where(Competitor.platform == platform)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((Competitor.name.ilike(like)) | (Competitor.nickname.ilike(like)))
        count_stmt = count_stmt.where((Competitor.name.ilike(like)) | (Competitor.nickname.ilike(like)))
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(Competitor.id.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return {"total": total, "items": rows}


@router.post("", response_model=CompetitorResponse, status_code=201)
async def add_competitor(body: CompetitorCreate, db: AsyncSession = Depends(get_db)):
    try:
        uid, homepage = parse_platform_uid(body.platform.value, body.platform_uid)
    except ValueError as e:
        raise HTTPException(400, str(e))

    dup = await db.execute(
        select(Competitor).where(
            Competitor.platform == body.platform.value,
            Competitor.platform_uid == uid,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, f"竞品已存在：platform={body.platform.value}, uid={uid}")

    comp = Competitor(
        name=body.name,
        platform=body.platform.value,
        platform_uid=uid,
        homepage_url=homepage,
        note=body.note,
    )
    db.add(comp)
    await db.commit()
    await db.refresh(comp)
    return comp


@router.put("/{competitor_id}", response_model=CompetitorResponse)
async def update_competitor(competitor_id: int, body: CompetitorUpdate,
                            db: AsyncSession = Depends(get_db)):
    comp = await db.get(Competitor, competitor_id)
    if not comp:
        raise HTTPException(404, f"Competitor not found: id={competitor_id}")
    if body.name is not None:
        comp.name = body.name
    if body.note is not None:
        comp.note = body.note
    if body.enabled is not None:
        comp.enabled = body.enabled
    await db.commit()
    await db.refresh(comp)
    return comp


@router.delete("/{competitor_id}")
async def delete_competitor(competitor_id: int, db: AsyncSession = Depends(get_db)):
    comp = await db.get(Competitor, competitor_id)
    if not comp:
        raise HTTPException(404, f"Competitor not found: id={competitor_id}")
    await db.delete(comp)
    await db.commit()
    return {"message": f"Deleted competitor id={competitor_id}"}


@router.post("/discover", response_model=DiscoverResponse)
async def discover(body: DiscoverRequest, db: AsyncSession = Depends(get_db)):
    """关键词搜索发现候选竞品账号。"""
    platform = body.platform.value
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(400, "关键词不能为空")

    message = ""
    if platform == "bilibili":
        candidates = await discover_bilibili(keyword, body.max_items,
                                             max_pages=body.max_pages)
        if not candidates:
            message = "搜索无结果或接口失败，可稍后重试"
    elif platform == "douyin":
        from backend.api.credentials import get_active_cookie
        cookie_str = await get_active_cookie("douyin", db)
        loop = asyncio.get_running_loop()
        candidates = await loop.run_in_executor(
            None, _run_coro_in_thread,
            discover_douyin(keyword, cookie_str=cookie_str, max_items=body.max_items,
                            max_pages=body.max_pages))
        if not candidates:
            message = ("抖音搜索需要登录：请在「凭证管理」保存抖音 Cookie 后重试，"
                       "或直接粘贴竞品主页链接手动添加")
    else:
        raise HTTPException(400, f"暂不支持平台：{platform}")

    return {
        "platform": platform,
        "keyword": keyword,
        "candidates": [
            DiscoveredCompetitor(
                platform=c.platform,
                platform_uid=c.platform_uid,
                nickname=c.nickname,
                signature=c.signature[:100],
                follower_count=c.follower_count,
                video_hits=c.video_hits,
                latest_pub_time=c.latest_pub_time,
                total_plays=c.total_plays,
                sample_titles=c.sample_titles,
                homepage_url=c.homepage_url,
            ) for c in candidates
        ],
        "message": message,
    }
