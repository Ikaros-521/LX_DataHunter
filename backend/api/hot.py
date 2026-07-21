"""热门内容爬取 API"""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db, AsyncSessionLocal
from backend.models import MonitoredCreator, CrawlSession, SessionStatus, TriggerType, beijing_now
from backend.engine.hot_crawler import crawl_hot, get_categories
from backend.api.credentials import get_active_cookie
from backend.api.ws import broadcast_progress

logger = logging.getLogger("lx_datahunter.hot")
router = APIRouter(prefix="/api/v1/hot", tags=["hot"])


class HotCrawlRequest(BaseModel):
    platform: str
    category: str
    max_items: int = Field(default=50, ge=1, le=200)
    keyword: str = Field(default="", description="搜索关键词 (category=search 时使用)")


@router.get("/categories")
async def list_categories(platform: str | None = None):
    """获取热门分类"""
    from backend.engine.hot_crawler import HOT_CATEGORIES, PLATFORM_LABELS
    if platform:
        cats = HOT_CATEGORIES.get(platform, [])
        return [{"platform": platform, **c} for c in cats]

    result = []
    for plat, cats in HOT_CATEGORIES.items():
        for c in cats:
            result.append({"platform": plat, "platform_name": PLATFORM_LABELS.get(plat, plat), **c})
    return result


@router.post("/crawl", status_code=202)
async def trigger_hot_crawl(
    body: HotCrawlRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """触发热门爬取"""
    # 验证分类
    cats = get_categories(body.platform)
    if not any(c["id"] == body.category for c in cats):
        raise HTTPException(400, f"Unknown category '{body.category}' for platform '{body.platform}'")

    # 查找/创建虚拟创作者
    from sqlalchemy import select
    virtual_uid = f"__hot__{body.platform}_{body.category}"
    result = await db.execute(
        select(MonitoredCreator).where(
            MonitoredCreator.platform == body.platform,
            MonitoredCreator.platform_uid == virtual_uid,
        )
    )
    creator = result.scalar_one_or_none()
    if not creator:
        cat_name = next((c["name"] for c in cats if c["id"] == body.category), body.category)
        creator = MonitoredCreator(
            platform=body.platform,
            platform_uid=virtual_uid,
            nickname=f"[热门] {cat_name}",
        )
        db.add(creator)
        await db.commit()
        await db.refresh(creator)

    # 搜索类任务：将关键词存入 creator.extra_data，供定时调度时读取
    if body.category == "search" and body.keyword:
        creator.extra_data = {**(creator.extra_data or {}), "keyword": body.keyword}
        await db.commit()
        await db.refresh(creator)

    # 创建 session
    session = CrawlSession(
        creator_id=creator.id,
        platform=body.platform,
        trigger_type=TriggerType.MANUAL.value,
        status=SessionStatus.RUNNING.value,
        keyword=body.keyword or "",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    logger.info(f"Hot crawl triggered: platform={body.platform}, category={body.category}, session_id={session.id}")

    background_tasks.add_task(_run_hot_crawl, body.platform, body.category, body.max_items, creator.id, session.id, body.keyword)

    return {"session_id": session.id, "message": f"Hot crawl started: {body.platform}/{body.category}"}


async def _run_hot_crawl(platform: str, category: str, max_items: int, creator_id: int, session_id: int, keyword: str = ""):
    """后台执行热门爬取"""
    from backend.engine.crawler import _sum_engagement
    from backend.models import ContentSnapshot

    async with AsyncSessionLocal() as db:
        try:
            # 获取凭证
            cookie_str = await get_active_cookie(platform, db)

            async def on_progress(current, total, status):
                await broadcast_progress(session_id, current, total, status)

            # 爬取
            creator_label, items = await crawl_hot(
                platform=platform,
                category=category,
                max_items=max_items,
                credential_str=cookie_str,
                progress_callback=on_progress,
                keyword=keyword,
            )

            # 更新创作者昵称
            creator = await db.get(MonitoredCreator, creator_id)
            if creator:
                creator.nickname = creator_label

            # 写入 snapshots
            for item in items:
                snap = ContentSnapshot(
                    session_id=session_id,
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

            # 完成 session
            engagement = _sum_engagement(items)
            session = await db.get(CrawlSession, session_id)
            if session:
                session.status = SessionStatus.COMPLETED.value
                session.finished_at = beijing_now()
                session.total_contents = len(items)
                session.engagement_total = engagement
                session.delta_from_prev = {}

            await db.commit()
            logger.info(f"Hot crawl completed: session_id={session_id}, items={len(items)}")

        except Exception as e:
            logger.error(f"Hot crawl failed: session_id={session_id}, error={e}", exc_info=True)
            session = await db.get(CrawlSession, session_id)
            if session:
                session.status = SessionStatus.FAILED.value
                session.error_message = str(e)[:500]
                session.finished_at = beijing_now()
                await db.commit()
            await broadcast_progress(session_id, 0, 0, "failed")
