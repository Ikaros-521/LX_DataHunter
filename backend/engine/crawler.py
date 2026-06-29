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
    keyword: str | None = None,
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

    # 热门/搜索类虚拟创作者 (uid 形如 __hot__{platform}_{category}) 不能走
    # 适配器的「按 UID 抓主页」流程（会访问不存在的 /user/__hot__...），
    # 必须转交给 crawl_hot。这里统一拦截，覆盖手动触发和定时调度两条路径。
    if creator.platform_uid.startswith("__hot__"):
        return await _crawl_hot_virtual(
            creator=creator,
            db=db,
            trigger_type=trigger_type,
            max_items=max_items,
            headless=headless,
            progress_callback=progress_callback,
            existing_session_id=existing_session_id,
            keyword=keyword,
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


async def _crawl_hot_virtual(
    creator: MonitoredCreator,
    db: AsyncSession,
    trigger_type: TriggerType = TriggerType.MANUAL,
    max_items: int = 50,
    headless: bool = True,
    progress_callback: Any = None,
    existing_session_id: Optional[int] = None,
    keyword: str | None = None,
) -> CrawlSession:
    """处理 __hot__{platform}_{category} 虚拟创作者 —— 转交给 crawl_hot。

    uid 形如 __hot__douyin_search：去掉前缀后剩余部分为 "{platform}_{category}"。
    platform 一定在 HOT_CATEGORIES 支持的平台集合内，剩余即为 category。
    """
    from .hot_crawler import crawl_hot
    from backend.api.credentials import get_active_cookie

    # 解析 platform / category：前缀之后的部分。注意 category 可能不含 '_'，
    # 但 platform 来自固定的几个已知平台，据此切分。
    payload = creator.platform_uid[len("__hot__"):]  # e.g. "douyin_search"
    category = payload
    # payload 形如 "{platform}_{category}"，platform 与 creator.platform 相同，
    # 去掉 "{platform}_" 前缀即为 category
    prefix = f"{creator.platform}_"
    if payload.startswith(prefix):
        category = payload[len(prefix):]

    # 1. Create or reuse session（与 crawl_creator 保持一致）
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
            keyword=keyword or "",
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        logger.info(f"Session created (hot virtual): id={session.id}")

    # search 类需要关键词：优先用传入的 keyword，其次从 creator.extra_data 读取
    if category == "search":
        if not keyword:
            keyword = (creator.extra_data or {}).get("keyword", "")
        if not keyword:
            msg = "搜索类任务需要关键词，不能直接触发。请在「热门内容」页输入关键词后再抓取。"
            logger.warning(f"Search virtual creator {creator.platform_uid} triggered without keyword.")
            session.status = SessionStatus.FAILED.value
            session.error_message = msg
            session.finished_at = beijing_now()
            await db.commit()
            raise ValueError(msg)

    logger.info(
        f"Hot virtual crawl: platform={creator.platform}, category={category}, "
        f"session={session.id}, max={max_items}, keyword={keyword or '(none)'}"
    )

    # 2. 获取凭证
    cookie_str = None
    try:
        cookie_str = await get_active_cookie(creator.platform, db)
    except Exception as e:
        logger.warning(f"Failed to look up credential: {e}")

    # 3. 执行 crawl_hot
    try:
        creator_label, items = await crawl_hot(
            platform=creator.platform,
            category=category,
            max_items=max_items,
            credential_str=cookie_str,
            progress_callback=progress_callback,
            keyword=keyword or "",
        )
        logger.info(f"crawl_hot returned: {len(items)} items, label='{creator_label}'")
    except Exception as e:
        logger.error(f"Hot virtual crawl failed: {e}", exc_info=True)
        session.status = SessionStatus.FAILED.value
        session.error_message = str(e)[:500]
        session.finished_at = beijing_now()
        await db.commit()
        raise

    # 4. 更新昵称
    if creator_label:
        creator.nickname = creator_label
    creator.updated_at = beijing_now()

    # 5. 写入 snapshots
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

    # 6. Finalize session
    engagement = _sum_engagement(items)
    session.status = SessionStatus.COMPLETED.value
    session.finished_at = beijing_now()
    session.total_contents = len(items)
    session.engagement_total = engagement
    session.delta_from_prev = {}

    await db.commit()
    await db.refresh(session)
    logger.info(f"Hot virtual crawl completed: session_id={session.id}, total={len(items)}")

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
