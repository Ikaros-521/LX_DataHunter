"""Crawler orchestrator: ties together adapter, DB, and WebSocket."""
import logging
from datetime import datetime
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    MonitoredCreator, CrawlSession, ContentSnapshot, SessionStatus, TriggerType, beijing_now,
)
from .platforms import get_adapter, ContentItem, CreatorInfo

logger = logging.getLogger("fapai.crawler")


async def crawl_creator(
    creator: MonitoredCreator,
    db: AsyncSession,
    trigger_type: TriggerType = TriggerType.MANUAL,
    max_items: int = 50,
    headless: bool = True,
    progress_callback: Any = None,
    existing_session_id: Optional[int] = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> CrawlSession:
    """Execute a crawl for a creator.

    如果提供了 existing_session_id，复用已有的 session（API 层预创建的），
    否则新建一个 session（定时调度等场景）。
    """
    adapter = get_adapter(creator.platform)

    logger.info(
        f"Starting crawl: creator_id={creator.id}, platform={creator.platform}, "
        f"uid={creator.platform_uid}, trigger={trigger_type.value}, max_items={max_items}"
    )

    # 1. Create or reuse session
    if existing_session_id:
        session = await db.get(CrawlSession, existing_session_id)
        if not session:
            raise ValueError(f"Session {existing_session_id} not found")
        logger.info(f"Reusing existing session: id={session.id}")
    else:
        session = CrawlSession(
            creator_id=creator.id,
            platform=creator.platform,
            trigger_type=trigger_type.value,
            status=SessionStatus.RUNNING.value,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        logger.info(f"Session created: id={session.id}")

    # 1.5 Look up active credential for this platform
    cookie_str = None
    try:
        from backend.api.credentials import get_active_cookie
        cookie_str = await get_active_cookie(creator.platform, db)
        if cookie_str:
            logger.info(f"Using active credential for platform={creator.platform}")
    except Exception as e:
        logger.warning(f"Failed to look up credential: {e}")

    # 2. Run crawler
    try:
        logger.info(f"Calling adapter.crawl_creator() for platform={creator.platform}...")
        creator_info, items = await adapter.crawl_creator(
            platform_uid=creator.platform_uid,
            max_items=max_items,
            headless=headless,
            progress_callback=progress_callback,
            cookie_str=cookie_str,
            date_from=date_from,
            date_to=date_to,
        )
        logger.info(f"Adapter returned: {len(items)} items, creator='{creator_info.nickname}'")
    except Exception as e:
        logger.error(f"Crawl failed: {e}", exc_info=True)
        session.status = SessionStatus.FAILED.value
        session.error_message = str(e)[:500]
        session.finished_at = beijing_now()
        await db.commit()
        raise

    # 3. Update creator info
    if creator_info.nickname:
        logger.info(f"Updating creator info: nickname='{creator_info.nickname}', "
                     f"followers={creator_info.follower_count}")
        creator.nickname = creator_info.nickname
    if creator_info.signature:
        creator.signature = creator_info.signature
    if creator_info.avatar_url:
        creator.avatar_url = creator_info.avatar_url
    if creator_info.follower_count:
        creator.follower_count = creator_info.follower_count
    if creator_info.extra_info:
        creator.extra_info = creator_info.extra_info
    creator.updated_at = beijing_now()

    # 4. Write content snapshots
    logger.info(f"Writing {len(items)} content snapshots to DB...")
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
    logger.info(
        f"Engagement total: likes={engagement['likes']}, comments={engagement['comments']}, "
        f"shares={engagement['shares']}, plays={engagement['plays']}"
    )

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
        logger.info(f"Delta from previous session: {delta}")
    else:
        logger.info("No previous session — first crawl for this creator")

    # 6. Finalize session
    session.status = SessionStatus.COMPLETED.value
    session.finished_at = beijing_now()
    session.total_contents = len(items)
    session.engagement_total = engagement
    session.delta_from_prev = delta

    await db.commit()
    await db.refresh(session)

    logger.info(f"Crawl completed: session_id={session.id}, total_contents={len(items)}")

    # 7. Final progress update
    if progress_callback:
        try:
            await progress_callback(len(items), len(items), "completed")
        except Exception:
            pass

    return session


def _sum_engagement(items: list[ContentItem]) -> dict:
    return {
        "likes": sum(i.likes for i in items),
        "comments": sum(i.comments for i in items),
        "shares": sum(i.shares for i in items),
        "plays": sum(i.plays for i in items),
        "collects": sum(i.collects for i in items),
    }
