"""WebSocket endpoint for crawl progress."""
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# In-memory registry: session_id -> list of connected websockets
_active_connections: dict[int, list[WebSocket]] = {}


async def broadcast_progress(session_id: int, current: int, total: int, status: str):
    """Push progress to all clients watching this session."""
    payload = json.dumps({"type": "progress", "current": current, "total": total, "status": status})
    connections = _active_connections.get(session_id, [])
    dead = []
    for ws in connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.remove(ws)


@router.websocket("/ws/crawl/{session_id}")
async def crawl_progress(websocket: WebSocket, session_id: int):
    await websocket.accept()
    _active_connections.setdefault(session_id, []).append(websocket)
    try:
        while True:
            # Keep connection alive, client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _active_connections.get(session_id, []).remove(websocket)
