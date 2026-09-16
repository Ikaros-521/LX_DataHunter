"""竞品调研任务 API：创建/查询/删除 + 报告文件服务 + 定时配置 + 关键词管理。"""
import csv
import io
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import (
    Competitor, ResearchComment, ResearchDanmaku, ResearchKeyword,
    ResearchSchedule, ResearchTask,
)
from backend.schemas import (
    MessageResponse, ResearchCreateRequest, ResearchDanmakuContentStat,
    ResearchDanmakuListResponse, ResearchKeywordCreate,
    ResearchKeywordListResponse, ResearchKeywordResponse,
    ResearchKeywordUpdate, ResearchListResponse, ResearchScheduleResponse,
    ResearchScheduleUpsert, ResearchTaskResponse,
)
from backend.engine.research_engine import research_engine
from backend.engine.report_builder import sanitize_report_title

logger = logging.getLogger("lx_datahunter.api.research")
router = APIRouter(prefix="/api/v1/research", tags=["research"])


def _task_response(task: ResearchTask) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "mode": task.mode or "accounts",
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "competitor_ids": task.competitor_ids or [],
        "keyword_ids": task.keyword_ids or [],
        "competitor_count": (len(task.competitor_ids or []) if (task.mode or "accounts") == "accounts"
                             else len(task.keyword_ids or [])),
        "params": task.params or {},
        "market_overview": task.market_overview or "",
        "markdown_path": task.markdown_path or "",
        "trigger_type": task.trigger_type,
        "error_message": task.error_message or "",
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _task_dir(task_id: int, title: str) -> Path:
    return settings.reports_dir / f"{task_id}_{sanitize_report_title(title)}"


async def start_research(
    db: AsyncSession,
    ids: list[int],
    params: dict,
    title: str = "",
    trigger_type: str = "manual",
    mode: str = "accounts",
) -> ResearchTask:
    """创建调研任务并启动（供 API 与定时调度共用）。

    mode=accounts 时 ids 为 competitor id；mode=keyword 时为 keyword id。
    """
    if mode == "keyword":
        result = await db.execute(
            select(ResearchKeyword).where(
                ResearchKeyword.id.in_(ids), ResearchKeyword.enabled == True)
            .order_by(ResearchKeyword.id)
        )
        targets = result.scalars().all()
    else:
        result = await db.execute(
            select(Competitor).where(Competitor.id.in_(ids), Competitor.enabled == True)
            .order_by(Competitor.id)
        )
        targets = result.scalars().all()
    if not targets:
        raise HTTPException(400, "没有可用的调研对象（ids 无效或均已停用）")
    valid_ids = [t.id for t in targets]

    if not title:
        if mode == "keyword":
            names = "、".join(t.keyword for t in targets[:3])
        else:
            names = "、".join((t.name or t.nickname or t.platform_uid) for t in targets[:3])
        suffix = "等" if len(targets) > 3 else ""
        title = f"{datetime.now().strftime('%Y%m%d')} {names}{suffix}"

    task = ResearchTask(
        title=title,
        mode=mode,
        status="pending",
        competitor_ids=valid_ids if mode == "accounts" else [],
        keyword_ids=valid_ids if mode == "keyword" else [],
        params=params,
        trigger_type=trigger_type,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    research_engine.submit(task.id)
    logger.info(f"Research task started: id={task.id}, mode={mode}, ids={valid_ids}, "
                f"trigger={trigger_type}")
    return task


@router.post("", response_model=ResearchTaskResponse, status_code=202)
async def create_research(body: ResearchCreateRequest, db: AsyncSession = Depends(get_db)):
    mode = body.mode if body.mode in ("accounts", "keyword") else "accounts"
    ids = body.keyword_ids if mode == "keyword" else body.competitor_ids
    if not ids:
        raise HTTPException(400, f"mode={mode} 需要提供对应的 id 列表")

    params = {
        "mode": mode,
        "date_from": body.date_from,
        "date_to": body.date_to,
        "max_videos_per_competitor": body.max_videos_per_competitor,
        "top_videos_for_comments": body.top_videos_for_comments,
        "max_comments_per_video": body.max_comments_per_video,
        "max_videos_per_keyword": body.max_videos_per_keyword,
        "max_search_pages": body.max_search_pages,
        "crawl_danmaku": body.crawl_danmaku,
        "max_danmakus_per_video": body.max_danmakus_per_video,
        "llm_filter_videos": body.llm_filter_videos,
    }
    task = await start_research(db, ids, params, title=body.title,
                                trigger_type="manual", mode=mode)
    return _task_response(task)


@router.get("", response_model=ResearchListResponse)
async def list_research(page: int = 1, page_size: int = 20,
                        db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(ResearchTask.id)))).scalar() or 0
    stmt = (select(ResearchTask).order_by(desc(ResearchTask.id))
            .offset((page - 1) * page_size).limit(page_size))
    rows = (await db.execute(stmt)).scalars().all()
    return {"total": total, "items": [_task_response(t) for t in rows]}


@router.get("/schedule", response_model=ResearchScheduleResponse)
async def get_schedule(db: AsyncSession = Depends(get_db)):
    sched = (await db.execute(select(ResearchSchedule).limit(1))).scalar_one_or_none()
    if not sched:
        sched = ResearchSchedule(params={})
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
    return sched


# ---- 调研关键词管理（mode=keyword 的数据源）----

@router.get("/keywords", response_model=ResearchKeywordListResponse)
async def list_keywords(page: int = 1, page_size: int = 100,
                        db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(ResearchKeyword.id)))).scalar() or 0
    stmt = (select(ResearchKeyword).order_by(desc(ResearchKeyword.id))
            .offset((page - 1) * page_size).limit(page_size))
    rows = (await db.execute(stmt)).scalars().all()
    return {"total": total, "items": rows}


@router.post("/keywords", response_model=ResearchKeywordResponse, status_code=201)
async def add_keyword(body: ResearchKeywordCreate, db: AsyncSession = Depends(get_db)):
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(400, "关键词不能为空")
    dup = await db.execute(
        select(ResearchKeyword).where(
            ResearchKeyword.keyword == keyword,
            ResearchKeyword.platform == body.platform.value,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, f"关键词已存在：{keyword}（{body.platform.value}）")
    kw = ResearchKeyword(keyword=keyword, platform=body.platform.value, note=body.note)
    db.add(kw)
    await db.commit()
    await db.refresh(kw)
    return kw


@router.put("/keywords/{keyword_id}", response_model=ResearchKeywordResponse)
async def update_keyword(keyword_id: int, body: ResearchKeywordUpdate,
                         db: AsyncSession = Depends(get_db)):
    kw = await db.get(ResearchKeyword, keyword_id)
    if not kw:
        raise HTTPException(404, f"Keyword not found: id={keyword_id}")
    if body.note is not None:
        kw.note = body.note
    if body.enabled is not None:
        kw.enabled = body.enabled
    await db.commit()
    await db.refresh(kw)
    return kw


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int, db: AsyncSession = Depends(get_db)):
    kw = await db.get(ResearchKeyword, keyword_id)
    if not kw:
        raise HTTPException(404, f"Keyword not found: id={keyword_id}")
    await db.delete(kw)
    await db.commit()
    return {"message": f"Deleted keyword id={keyword_id}"}


@router.put("/schedule", response_model=ResearchScheduleResponse)
async def upsert_schedule(body: ResearchScheduleUpsert, db: AsyncSession = Depends(get_db)):
    sched = (await db.execute(select(ResearchSchedule).limit(1))).scalar_one_or_none()
    if not sched:
        sched = ResearchSchedule(params=body.params)
        db.add(sched)
    sched.interval_minutes = body.interval_minutes
    sched.enabled = body.enabled
    sched.params = body.params
    if body.enabled:
        sched.next_run_at = datetime.now() + timedelta(minutes=body.interval_minutes)
    else:
        sched.next_run_at = None
    await db.commit()
    await db.refresh(sched)
    return sched


@router.get("/{research_id}", response_model=ResearchTaskResponse)
async def get_research(research_id: int, db: AsyncSession = Depends(get_db)):
    task = await db.get(ResearchTask, research_id)
    if not task:
        raise HTTPException(404, f"Research task not found: id={research_id}")
    return _task_response(task)


@router.delete("/{research_id}")
async def delete_research(research_id: int, db: AsyncSession = Depends(get_db)):
    task = await db.get(ResearchTask, research_id)
    if not task:
        raise HTTPException(404, f"Research task not found: id={research_id}")
    if task.status not in ("completed", "failed"):
        raise HTTPException(409, "任务仍在运行中，无法删除")

    await db.execute(delete(ResearchComment).where(ResearchComment.research_task_id == task.id))
    await db.delete(task)
    await db.commit()

    task_dir = _task_dir(task.id, task.title)
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    return {"message": f"Deleted research task id={research_id}"}


@router.get("/{research_id}/danmakus/export")
async def export_research_danmakus(research_id: int,
                                   db: AsyncSession = Depends(get_db)):
    """任务全部原始弹幕导出 CSV（utf-8-sig，Excel 可直接打开）。"""
    task = await db.get(ResearchTask, research_id)
    if not task:
        raise HTTPException(404, f"Research task not found: id={research_id}")
    rows = (await db.execute(
        select(ResearchDanmaku)
        .where(ResearchDanmaku.research_task_id == research_id)
        .order_by(ResearchDanmaku.content_id, ResearchDanmaku.dm_time, ResearchDanmaku.id)
    )).scalars().all()

    titles: dict[str, str] = {}
    for s in (task.competitor_stats or []):
        for v in s.get("videos") or []:
            if v.get("content_id"):
                titles[str(v["content_id"])] = v.get("title", "")

    mode_label = {1: "滚动", 4: "底部", 5: "顶部"}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["content_id", "视频标题", "danmaku_id", "弹幕内容", "发送时间",
                     "视频内秒数", "模式", "字号", "颜色", "用户哈希", "池"])
    for r in rows:
        writer.writerow([
            r.content_id, titles.get(r.content_id, ""), r.danmaku_id, r.text,
            r.send_time.strftime("%Y-%m-%d %H:%M:%S") if r.send_time else "",
            r.dm_time, mode_label.get(r.mode, r.mode), r.font_size,
            r.color, r.user_hash, r.pool,
        ])
    return Response(
        content=buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="danmaku_{task.id}.csv"'},
    )


@router.get("/{research_id}/danmakus", response_model=ResearchDanmakuListResponse)
async def list_research_danmakus(
    research_id: int, competitor_id: int | None = None, content_id: str | None = None,
    page: int = 1, page_size: int = 50, db: AsyncSession = Depends(get_db),
):
    """调研任务抓取的原始弹幕（分页，可按视频筛选；附各视频条数统计）。"""
    task = await db.get(ResearchTask, research_id)
    if not task:
        raise HTTPException(404, f"Research task not found: id={research_id}")

    conds = [ResearchDanmaku.research_task_id == research_id]
    if competitor_id is not None:
        conds.append(ResearchDanmaku.competitor_id == competitor_id)
    if content_id:
        conds.append(ResearchDanmaku.content_id == content_id)

    total = (await db.execute(
        select(func.count(ResearchDanmaku.id)).where(*conds))).scalar() or 0
    items = (await db.execute(
        select(ResearchDanmaku).where(*conds)
        .order_by(ResearchDanmaku.dm_time, ResearchDanmaku.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    # 各视频弹幕条数（不受筛选影响，供下拉）
    stats = (await db.execute(
        select(ResearchDanmaku.content_id, func.count(ResearchDanmaku.id))
        .where(ResearchDanmaku.research_task_id == research_id)
        .group_by(ResearchDanmaku.content_id))).all()
    titles: dict[str, str] = {}
    for s in (task.competitor_stats or []):
        for v in s.get("videos") or []:
            if v.get("content_id"):
                titles[str(v["content_id"])] = v.get("title", "")
    contents = [ResearchDanmakuContentStat(content_id=cid, title=titles.get(cid, ""), count=n)
                for cid, n in sorted(stats, key=lambda x: -x[1])]
    return {"total": total, "contents": contents, "items": items}


@router.get("/{research_id}/file")
async def get_report_file(research_id: int, download: bool = False,
                          db: AsyncSession = Depends(get_db)):
    """报告 Markdown 文件（在线预览或下载）。"""
    task = await db.get(ResearchTask, research_id)
    if not task:
        raise HTTPException(404, f"Research task not found: id={research_id}")
    if not task.markdown_path:
        raise HTTPException(404, "报告尚未生成")
    path = Path(task.markdown_path)
    if not path.exists():
        raise HTTPException(404, "报告文件已丢失（可能被手动删除）")
    # 确认文件位于报告目录下，防路径穿越
    if not path.resolve().is_relative_to(settings.reports_dir.resolve()):
        raise HTTPException(400, "非法的报告路径")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="report_{task.id}.md"'
    return FileResponse(str(path), media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/{research_id}/assets/{name}")
async def get_report_asset(research_id: int, name: str,
                           db: AsyncSession = Depends(get_db)):
    """报告截图资源。"""
    task = await db.get(ResearchTask, research_id)
    if not task:
        raise HTTPException(404, f"Research task not found: id={research_id}")

    assets_dir = _task_dir(task.id, task.title) / "assets"
    path = (assets_dir / name).resolve()
    if not path.is_relative_to(assets_dir.resolve()) or not path.exists():
        raise HTTPException(404, f"Asset not found: {name}")
    return FileResponse(str(path), media_type="image/png")
