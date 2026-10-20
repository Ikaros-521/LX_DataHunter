"""单视频链接爬取 API — 粘贴视频链接直接抓取入库。"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import (
    MonitoredCreator, CrawlSession, ContentSnapshot, SessionStatus, TriggerType, beijing_now,
)
from backend.engine.single_video import crawl_single_video
from backend.engine.crawler import _sum_engagement
from backend.api.credentials import get_active_cookie

logger = logging.getLogger("lx_datahunter.single_video")
router = APIRouter(prefix="/api/v1/single-video", tags=["single-video"])


class SingleVideoCrawlRequest(BaseModel):
    url: str = Field(..., min_length=5, max_length=2000, description="B站/抖音视频页或分享短链")
    headless: bool = Field(default=True, description="抖音路径是否无头浏览器")


@router.post("/crawl", status_code=200)
async def crawl_by_url(body: SingleVideoCrawlRequest, db: AsyncSession = Depends(get_db)):
    """解析视频链接并抓取该视频（同步返回）。

    视频作者会自动登记为创作者（已存在则复用并回填信息），
    内容写入一个一次性会话，可在「内容检索」中查到。
    """
    # 1. 解析链接（失败 → 400）
    from backend.engine.single_video import parse_video_url
    try:
        platform, video_id, _canonical = await parse_video_url(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 2. 取平台凭证并抓取
    cookie_str = None
    try:
        cookie_str = await get_active_cookie(platform, db)
    except Exception as e:
        logger.warning(f"Failed to look up credential: {e}")

    try:
        _, _, creator_info, item = await crawl_single_video(
            body.url, cookie_str=cookie_str, headless=body.headless
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Single video crawl failed: {e}", exc_info=True)
        raise HTTPException(502, f"视频抓取失败：{e}")

    # 3. 登记/更新作者为创作者
    uid = (
        creator_info.extra_info.get("sec_uid")
        or creator_info.extra_info.get("uid")
        or creator_info.extra_info.get("mid")
    )
    if not uid:
        raise HTTPException(502, "未能获取视频作者信息，无法入库")
    if len(uid) > 255:
        uid = uid[:255]

    result = await db.execute(
        select(MonitoredCreator).where(
            MonitoredCreator.platform == platform.value,
            MonitoredCreator.platform_uid == uid,
        )
    )
    creator = result.scalar_one_or_none()
    created_creator = False
    if creator:
        if creator_info.nickname:
            creator.nickname = creator_info.nickname
        if creator_info.signature:
            creator.signature = creator_info.signature
        if creator_info.avatar_url:
            creator.avatar_url = creator_info.avatar_url
        if creator_info.follower_count:
            creator.follower_count = creator_info.follower_count
        creator.updated_at = beijing_now()
    else:
        creator = MonitoredCreator(
            platform=platform.value,
            platform_uid=uid,
            nickname=creator_info.nickname or uid,
            signature=creator_info.signature,
            avatar_url=creator_info.avatar_url,
            follower_count=creator_info.follower_count,
            extra_info=creator_info.extra_info or {},
        )
        db.add(creator)
        created_creator = True
    await db.flush()

    # 4. 建会话 + 写内容快照（单条，直接落为已完成）
    session = CrawlSession(
        creator_id=creator.id,
        platform=platform.value,
        trigger_type=TriggerType.MANUAL.value,
        status=SessionStatus.RUNNING.value,
    )
    db.add(session)
    await db.flush()

    db.add(ContentSnapshot(
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
    ))

    session.status = SessionStatus.COMPLETED.value
    session.finished_at = beijing_now()
    session.total_contents = 1
    session.engagement_total = _sum_engagement([item])
    session.delta_from_prev = {}
    await db.commit()
    await db.refresh(session)

    logger.info(
        f"Single video crawled: session_id={session.id}, creator_id={creator.id} "
        f"(new={created_creator}), platform={platform.value}, video={item.content_id}"
    )

    return {
        "session_id": session.id,
        "creator_id": creator.id,
        "creator_new": created_creator,
        "creator_nickname": creator.nickname,
        "platform": platform.value,
        "video_id": item.content_id,
        "content": {
            "title": item.title,
            "cover_url": item.cover_url,
            "url": item.url,
            "duration_sec": item.duration_sec,
            "create_time": item.create_time.isoformat() if item.create_time else None,
            "likes": item.likes,
            "comments": item.comments,
            "shares": item.shares,
            "plays": item.plays,
            "collects": item.collects,
        },
    }
