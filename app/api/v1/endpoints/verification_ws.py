"""
WebSocket endpoint for real-time verification status updates.

  WS /api/v1/ws/verify-face/{user_id}
"""
import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.verification import VerificationAttempt

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])


class VerificationWatcher:
    """Watches a user's verification attempts and pushes updates to WebSocket clients."""

    def __init__(self):
        self.connections: dict[UUID, list[WebSocket]] = {}

    def add_client(self, user_id: UUID, ws: WebSocket):
        if user_id not in self.connections:
            self.connections[user_id] = []
        self.connections[user_id].append(ws)
        _log.info("ws | user=%s connected, total=%d", user_id, len(self.connections[user_id]))

    def remove_client(self, user_id: UUID, ws: WebSocket):
        if user_id in self.connections:
            self.connections[user_id] = [c for c in self.connections[user_id] if c != ws]
            if not self.connections[user_id]:
                del self.connections[user_id]
        _log.info("ws | user=%s disconnected", user_id)

    async def notify(self, user_id: UUID, message: dict):
        """Send a message to all connected clients for this user."""
        if user_id not in self.connections:
            return
        dead = []
        for ws in self.connections[user_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_client(user_id, ws)


watcher = VerificationWatcher()


@router.websocket("/verify-face/{user_id}")
async def verification_status_ws(websocket: WebSocket, user_id: UUID):
    """
    Real-time verification status updates.
    Opens a new DB session per poll so we always read fresh data (no session cache).
    Sends updates every 2s until verified/rejected.
    Optional query param: ?type=id  to watch ID verification instead of face.
    """
    attempt_type = websocket.query_params.get("type", "face")
    await websocket.accept()
    watcher.add_client(user_id, websocket)

    try:
        while True:
            # Fresh session each iteration — avoids SQLAlchemy identity-map caching
            # the same stale row and never seeing the background task's update.
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(VerificationAttempt)
                    .where(VerificationAttempt.user_id == user_id)
                    .where(VerificationAttempt.attempt_type == attempt_type)
                    .order_by(VerificationAttempt.submitted_at.desc())
                    .limit(1)
                )
                attempt = result.scalar_one_or_none()

            if attempt:
                payload = {
                    "status":              attempt.status,
                    "face_match_score":    attempt.face_match_score,
                    "id_face_match_score": attempt.id_face_match_score,
                    "rejection_reason":    attempt.rejection_reason,
                    "is_live":             attempt.is_live,
                    "id_has_name":         attempt.id_has_name,
                    "id_has_dob":          attempt.id_has_dob,
                    "id_has_expiry":       attempt.id_has_expiry,
                    "id_has_number":       attempt.id_has_number,
                    "processed_at":        attempt.processed_at.isoformat() if attempt.processed_at else None,
                }
                await websocket.send_json(payload)
                _log.info("ws | user=%s status=%s", user_id, attempt.status)

                # Stop polling once finalized
                if attempt.status in ("verified", "rejected"):
                    break

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        _log.error("ws | user=%s error: %s", user_id, exc, exc_info=True)
    finally:
        watcher.remove_client(user_id, websocket)
