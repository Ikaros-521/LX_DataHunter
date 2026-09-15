"""竞品调研进度 WebSocket 广播端点。结构同 analysis_ws.py。"""
import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("lx_datahunter.api.research_ws")
router = APIRouter()

_active_connections: Set[WebSocket] = set()


async def broadcast_research_progress(data: dict):
    """向所有活跃连接广播调研进度。供 ResearchEngine 通过 set_broadcast 注入调用。"""
    if not _active_connections:
        return
    message = json.dumps(data, ensure_ascii=False, default=str)
    dead = set()
    for ws in _active_connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _active_connections -= dead


@router.websocket("/ws/research")
async def research_progress_ws(websocket: WebSocket):
    """调研进度 WebSocket 端点 — 广播模式。"""
    await websocket.accept()
    _active_connections.add(websocket)
    logger.debug(f"Research WS client connected (total: {len(_active_connections)})")
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        _active_connections.discard(websocket)
        logger.debug(f"Research WS client disconnected (total: {len(_active_connections)})")
