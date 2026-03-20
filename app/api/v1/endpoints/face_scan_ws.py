"""
WebSocket endpoint for real-time face-scan-required status.

  WS /api/v1/ws/face-scan-required/{user_id}?token=<jwt>

On connect:
  - Verify JWT and that the requesting user matches user_id.
  - Send the current face_scan_required value immediately.
  - Keep alive with heartbeats every 20 s.
  - When face_scan_required changes (set by admin/moderation), push the new
    value via FaceScanWatcher.notify() so the client updates instantly.
"""
import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import decode_access_token
from app.db.session import AsyncSessionLocal
from app.models.user import User

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])

_HEARTBEAT_INTERVAL = 20  # seconds


# ─── Push registry ────────────────────────────────────────────────────────────

class FaceScanWatcher:
    """
    One asyncio.Queue per connected socket.
    Call notify(user_id, required) from admin/moderation code to push updates.
    """

    def __init__(self) -> None:
        self._queues: dict[UUID, list[asyncio.Queue]] = {}

    def _add(self, user_id: UUID, q: asyncio.Queue) -> None:
        self._queues.setdefault(user_id, []).append(q)

    def _remove(self, user_id: UUID, q: asyncio.Queue) -> None:
        if user_id in self._queues:
            self._queues[user_id] = [x for x in self._queues[user_id] if x is not q]
            if not self._queues[user_id]:
                del self._queues[user_id]

    async def notify(self, user_id: UUID, required: bool) -> None:
        for q in list(self._queues.get(user_id, [])):
            try:
                q.put_nowait({"required": required})
            except asyncio.QueueFull:
                pass


face_scan_watcher = FaceScanWatcher()


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@router.websocket("/face-scan-required/{user_id}")
async def face_scan_required_ws(
    websocket: WebSocket,
    user_id: UUID,
    token: str = Query(..., description="JWT access token"),
) -> None:
    """
    Pushes face_scan_required updates to the connected client.

    Message format (JSON):
      {"required": true | false}        — status update
      {"type": "heartbeat"}             — keep-alive (client should ignore)
    """
    await websocket.accept()

    # ── Auth ──────────────────────────────────────────────────────────────────
    try:
        payload = decode_access_token(token)
        token_user_id = payload.get("sub")
        if not token_user_id:
            await websocket.close(code=4001)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    # Only the owner of the profile may subscribe
    if str(user_id) != str(token_user_id):
        await websocket.close(code=4003)
        return

    # ── Fetch current value ───────────────────────────────────────────────────
    try:
        async with AsyncSessionLocal() as db:
            row = await db.execute(select(User).where(User.id == user_id))
            user = row.scalar_one_or_none()
    except Exception as exc:
        _log.warning("face_scan_ws | DB error for user=%s: %s", user_id, exc)
        await websocket.close(code=1011)
        return

    if not user:
        await websocket.close(code=4004)
        return

    # Send current state immediately
    try:
        await websocket.send_json({"required": user.face_scan_required})
    except Exception:
        return

    # ── Wait for push updates ─────────────────────────────────────────────────
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    face_scan_watcher._add(user_id, q)

    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        _log.error("face_scan_ws | user=%s error: %s", user_id, exc, exc_info=True)
    finally:
        face_scan_watcher._remove(user_id, q)
        _log.info("face_scan_ws | user=%s disconnected", user_id)
