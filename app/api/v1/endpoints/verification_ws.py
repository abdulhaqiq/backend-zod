"""
WebSocket endpoint for real-time verification status updates.

  WS /api/v1/ws/verify-face/{user_id}

Architecture: purely push-driven.
  - On connect: check DB once. If already resolved → send result and close.
  - If still pending: register in VerificationWatcher and wait.
  - The _process_verification background task calls watcher.notify() the instant
    it has a result. The WS handler receives it, sends to the client, and closes.
  - Every 20 s a heartbeat ping is sent so the mobile OS doesn't kill the socket.
  - No polling loop → no redundant DB reads, no race between poll and notify.
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


# ─── Watcher ──────────────────────────────────────────────────────────────────

class VerificationWatcher:
    """
    Push-only registry.  background task calls notify() → message is sent to all
    connected WS clients for that user → each handler closes its connection.
    """

    def __init__(self):
        # user_id → list of asyncio.Queue (one per connected WS)
        self._queues: dict[UUID, list[asyncio.Queue]] = {}

    def _add(self, user_id: UUID, q: asyncio.Queue) -> None:
        self._queues.setdefault(user_id, []).append(q)
        _log.info("ws | user=%s connected (active=%d)", user_id, len(self._queues[user_id]))

    def _remove(self, user_id: UUID, q: asyncio.Queue) -> None:
        if user_id in self._queues:
            self._queues[user_id] = [x for x in self._queues[user_id] if x is not q]
            if not self._queues[user_id]:
                del self._queues[user_id]
        _log.info("ws | user=%s disconnected", user_id)

    async def notify(self, user_id: UUID, message: dict) -> None:
        """Called by _process_verification when result is ready. Non-blocking."""
        for q in list(self._queues.get(user_id, [])):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass


watcher = VerificationWatcher()

_SENTINEL = object()          # signals the handler to close
_HEARTBEAT_INTERVAL = 20      # seconds between keep-alive pings


# ─── Endpoint ─────────────────────────────────────────────────────────────────

async def _fetch_attempt(user_id: UUID, attempt_type: str) -> VerificationAttempt | None:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            select(VerificationAttempt)
            .where(VerificationAttempt.user_id == user_id)
            .where(VerificationAttempt.attempt_type == attempt_type)
            .order_by(VerificationAttempt.submitted_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()


@router.websocket("/verify-face/{user_id}")
async def verification_status_ws(websocket: WebSocket, user_id: UUID) -> None:
    """
    Real-time verification status.
    Optional query param: ?type=id  to watch ID verification instead of face.
    """
    attempt_type = websocket.query_params.get("type", "face")
    await websocket.accept()
    _log.info("ws | user=%s type=%s accepted", user_id, attempt_type)

    # ── Register in watcher BEFORE DB check to eliminate race condition ─────────
    # If bg task finishes between our DB read and registration, we'd miss the
    # notify(). By registering first, we guarantee we catch every notification.
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    watcher._add(user_id, q)

    async def _send_result(attempt: VerificationAttempt) -> None:
        try:
            await websocket.send_json(_attempt_payload(attempt))
            _log.info("ws | user=%s result=%s sent", user_id, attempt.status)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        # ── Check if already resolved (covers: re-open after scan, fast bg task) ──
        attempt = await _fetch_attempt(user_id, attempt_type)
        if attempt and attempt.status in ("verified", "rejected"):
            await _send_result(attempt)
            return

        # ── Pending — wait for background task to push the result ──────────────
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                # Got the real result from notify()
                try:
                    await websocket.send_json(msg)
                    _log.info("ws | user=%s pushed %s", user_id, msg.get("status"))
                except (WebSocketDisconnect, Exception):
                    pass
                break
            except asyncio.TimeoutError:
                # Send a heartbeat so the socket stays alive on mobile
                try:
                    await websocket.send_json({"status": "heartbeat"})
                except Exception:
                    break

    except WebSocketDisconnect:
        _log.info("ws | user=%s client disconnected before result", user_id)
    except Exception as exc:
        _log.error("ws | user=%s error: %s", user_id, exc, exc_info=True)
    finally:
        watcher._remove(user_id, q)


def _attempt_payload(attempt: VerificationAttempt) -> dict:
    return {
        "status":              attempt.status,
        "face_match_score":    attempt.face_match_score,
        "id_face_match_score": attempt.id_face_match_score,
        "rejection_reason":    attempt.rejection_reason,
        "is_live":             attempt.is_live,
        "id_has_name":         attempt.id_has_name,
        "id_has_dob":          attempt.id_has_dob,
        "id_has_expiry":       attempt.id_has_expiry,
        "id_has_number":       attempt.id_has_number,
        "id_name_match":       attempt.id_name_match,
        "id_dob_match":        attempt.id_dob_match,
        "processed_at":        attempt.processed_at.isoformat() if attempt.processed_at else None,
    }
