"""下载进度 WebSocket 广播端点。"""
import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("fapai.api.download_ws")
router = APIRouter()

# 活跃连接注册表
_active_connections: Set[WebSocket] = set()


async def broadcast_download_progress(data: dict):
    """向所有活跃连接广播下载进度。"""
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


@router.websocket("/ws/downloads")
async def download_progress_ws(websocket: WebSocket):
    """下载进度 WebSocket 端点 — 广播模式。"""
    await websocket.accept()
    _active_connections.add(websocket)
    logger.debug(f"Download WS client connected (total: {len(_active_connections)})")
    try:
        # 保持连接，接收心跳
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # 发送心跳检测连接
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        _active_connections.discard(websocket)
        logger.debug(f"Download WS client disconnected (total: {len(_active_connections)})")
