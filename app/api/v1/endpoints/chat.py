"""
Chat API — real-time messaging via WebSocket + REST fallback.

WebSocket:
  WS /api/v1/ws/chat/{other_user_id}?token=<access_jwt>

REST:
  GET  /api/v1/chat/conversations          — list all conversations + last message
  GET  /api/v1/chat/{other_user_id}/messages — paginated history (newest first)
  POST /api/v1/chat/{other_user_id}/messages — send a message
  PUT  /api/v1/chat/{other_user_id}/read     — mark messages from other_user as read
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.push import send_push_notification, notify_user
from app.db.session import AsyncSessionLocal, get_db
from app.models.message import Message
from app.models.message_reaction import MessageReaction
from app.models.user import User
from app.models.user_report import UserReport

_log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])
ws_router = APIRouter(tags=["chat-ws"])


# ── Connection Managers ───────────────────────────────────────────────────────

class ConnectionManager:
    """Tracks a single active WebSocket per user (last connection wins)."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    def connect(self, user_id: str, ws: WebSocket):
        self._connections[user_id] = ws
        _log.info("ws:chat | user=%s connected, online=%d", user_id[:8], len(self._connections))

    def disconnect(self, user_id: str):
        self._connections.pop(user_id, None)
        _log.info("ws:chat | user=%s disconnected, online=%d", user_id[:8], len(self._connections))

    def is_online(self, user_id: str) -> bool:
        return user_id in self._connections

    async def send_to(self, user_id: str, payload: dict) -> bool:
        ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(payload))
            return True
        except Exception as exc:
            _log.warning("ws:chat | send_to %s failed: %s", user_id[:8], exc)
            self.disconnect(user_id)
            return False


class MultiConnectionManager:
    """Tracks ALL active WebSocket connections per user (fan-out delivery).

    Used for the /ws/notify channel so a user can have both a global
    CallContext socket and a per-screen socket open simultaneously without
    one overwriting the other.
    """

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    def connect(self, user_id: str, ws: WebSocket):
        self._connections.setdefault(user_id, []).append(ws)
        _log.info("ws:notify | user=%s connected, sessions=%d", user_id[:8], len(self._connections[user_id]))

    def disconnect(self, user_id: str, ws: WebSocket):
        bucket = self._connections.get(user_id, [])
        self._connections[user_id] = [c for c in bucket if c is not ws]
        if not self._connections[user_id]:
            del self._connections[user_id]
        _log.info("ws:notify | user=%s session removed", user_id[:8])

    def is_online(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))

    async def send_to(self, user_id: str, payload: dict) -> bool:
        connections = list(self._connections.get(user_id, []))
        if not connections:
            return False
        dead: list[WebSocket] = []
        sent = False
        for ws in connections:
            try:
                await ws.send_text(json.dumps(payload))
                sent = True
            except Exception as exc:
                _log.warning("ws:notify | send_to %s failed: %s", user_id[:8], exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)
        return sent


manager = ConnectionManager()

# Fan-out notify manager — supports multiple concurrent sessions per user
notify_manager = MultiConnectionManager()


# ── WebSocket: notifications ───────────────────────────────────────────────────

@ws_router.websocket("/ws/notify")
async def websocket_notify(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
):
    """
    General-purpose notification WebSocket.

    Client connects:
      ws://host/api/v1/ws/notify?token=<jwt>

    Server pushes JSON events:
      {"type": "liked_you",  "profile": { ...profile fields } }
      {"type": "match",      "profile": { ...profile fields } }
      {"type": "ping"}
    """
    # Accept first so we can always send proper close codes
    # and avoid uvicorn trying to write a 500 on an unaccepted transport.
    await websocket.accept()

    try:
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4001)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    try:
        async with AsyncSessionLocal() as db:
            current_user = await _get_user_by_id(db, user_id)
    except Exception as exc:
        _log.warning("ws:notify | DB unavailable, closing cleanly: %s", exc)
        await websocket.close(code=1011)
        return

    if not current_user:
        await websocket.close(code=4004)
        return

    uid_me = str(current_user.id)
    notify_manager.connect(uid_me, websocket)

    # Broadcast "online" to all matched partners immediately
    await _broadcast_presence(uid_me, online=True)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                msg_type = data.get("type", "")

                # ── Ping ──────────────────────────────────────────────────────
                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

                # ── Presence query ────────────────────────────────────────────
                elif msg_type == "presence_query":
                    query_uid = str(data.get("user_id", ""))
                    if query_uid:
                        is_on = notify_manager.is_online(query_uid) or manager.is_online(query_uid)
                        await websocket.send_text(json.dumps({
                            "type": "presence_status",
                            "user_id": query_uid,
                            "online": is_on,
                        }))

                # ── Call invite ───────────────────────────────────────────────
                elif msg_type == "call_invite":
                    to_uid = str(data.get("to", ""))
                    if not to_uid:
                        continue
                    async with AsyncSessionLocal() as db:
                        me     = await _get_user_by_id(db, uid_me)
                        target = await _get_user_by_id(db, to_uid)
                        if not (me and target):
                            continue
                        caller_name  = me.full_name or "Someone"
                        caller_image = (me.photos or [None])[0]
                        room_id      = Message.make_room_id(me.id, target.id)
                        other_push_token = target.push_token
                    delivered = await notify_manager.send_to(to_uid, {
                        "type":         "call_invite",
                        "from":         uid_me,
                        "caller_name":  caller_name,
                        "caller_image": caller_image,
                    })
                    if not delivered:
                        await send_push_notification(
                            other_push_token,
                            title=f"📞 {caller_name} is calling…",
                            body="Tap to answer",
                            data={
                                "type":         "call",
                                "from":         uid_me,
                                "caller_name":  caller_name,
                                "caller_image": caller_image,
                                "room_id":      room_id,
                            },
                        )

                # ── Call accept ───────────────────────────────────────────────
                elif msg_type == "call_accept":
                    to_uid = str(data.get("to", ""))
                    if to_uid:
                        await notify_manager.send_to(to_uid, {
                            "type": "call_accept",
                            "from": uid_me,
                        })

                # ── Call decline ──────────────────────────────────────────────
                # Only relay the signal — no DB record here.
                # The caller's frontend will auto-send call_end (duration=0)
                # which creates the single "Missed call" record.
                elif msg_type == "call_decline":
                    to_uid = str(data.get("to", ""))
                    if not to_uid:
                        continue
                    await notify_manager.send_to(to_uid, {
                        "type": "call_decline",
                        "from": uid_me,
                    })

                # ── WebRTC SDP offer (caller → callee) ───────────────────────
                elif msg_type == "sdp_offer":
                    to_uid = str(data.get("to", ""))
                    if to_uid:
                        await notify_manager.send_to(to_uid, {
                            "type": "sdp_offer",
                            "from": uid_me,
                            "sdp":  data.get("sdp"),
                        })

                # ── WebRTC SDP answer (callee → caller) ───────────────────────
                elif msg_type == "sdp_answer":
                    to_uid = str(data.get("to", ""))
                    if to_uid:
                        await notify_manager.send_to(to_uid, {
                            "type": "sdp_answer",
                            "from": uid_me,
                            "sdp":  data.get("sdp"),
                        })

                # ── WebRTC ICE candidate (both directions) ────────────────────
                elif msg_type == "ice_candidate":
                    to_uid = str(data.get("to", ""))
                    if to_uid:
                        await notify_manager.send_to(to_uid, {
                            "type":      "ice_candidate",
                            "from":      uid_me,
                            "candidate": data.get("candidate"),
                        })

                # ── Call end ──────────────────────────────────────────────────
                elif msg_type == "call_end":
                    to_uid   = str(data.get("to", ""))
                    duration = int(data.get("duration", 0))
                    if not to_uid:
                        continue
                    await notify_manager.send_to(to_uid, {
                        "type":     "call_end",
                        "from":     uid_me,
                        "duration": duration,
                    })
                    call_type = "ended" if duration > 0 else "missed"
                    async with AsyncSessionLocal() as db:
                        me     = await _get_user_by_id(db, uid_me)
                        target = await _get_user_by_id(db, to_uid)
                        if me and target:
                            room_id = Message.make_room_id(me.id, target.id)
                            # Deduplication: skip if a call record exists in last 30 s
                            from datetime import timedelta
                            from sqlalchemy import select as sa_select
                            cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
                            existing = (await db.execute(
                                sa_select(Message).where(
                                    Message.room_id == room_id,
                                    Message.msg_type == "call",
                                    Message.created_at > cutoff,
                                )
                            )).scalar_one_or_none()
                            if existing is None:
                                call_msg = Message(
                                    room_id=room_id,
                                    sender_id=me.id,
                                    receiver_id=target.id,
                                    content="",
                                    msg_type="call",
                                    extra={"call_type": call_type, "duration": duration},
                                    is_read=False,
                                    created_at=datetime.now(timezone.utc),
                                )
                                db.add(call_msg)
                                await db.commit()
                                await db.refresh(call_msg)
                                rec = {"type": "call_record", **_msg_to_dict(call_msg)}
                                await notify_manager.send_to(uid_me, rec)
                                await notify_manager.send_to(to_uid,  rec)

            except Exception:
                pass
    except (WebSocketDisconnect, OSError):
        pass
    except Exception as exc:
        _log.debug("ws:notify | connection dropped for user=%s: %s", uid_me[:8], exc)
    finally:
        notify_manager.disconnect(uid_me, websocket)
        # Only broadcast "offline" if this was the user's last notify session.
        # Suppress CancelledError during server shutdown — presence fan-out is
        # best-effort and must not block the shutdown sequence.
        if not notify_manager.is_online(uid_me):
            try:
                await _broadcast_presence(uid_me, online=False)
            except Exception:
                pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _msg_to_dict(m: Message, reactions: list[dict] | None = None) -> dict[str, Any]:
    return {
        "id":          str(m.id),
        "room_id":     m.room_id,
        "sender_id":   str(m.sender_id),
        "receiver_id": str(m.receiver_id),
        "content":     m.content,
        "msg_type":    m.msg_type,
        "metadata":    m.extra,
        "is_read":     m.is_read,
        "read_at":     m.read_at.isoformat() if m.read_at else None,
        "edited_at":   m.edited_at.isoformat() if m.edited_at else None,
        "created_at":  m.created_at.isoformat(),
        "reactions":   reactions or [],
    }


async def _get_reactions(db: AsyncSession, message_id: uuid.UUID) -> list[dict]:
    """Return all reactions for a message as [{emoji, user_id, created_at}]."""
    rows = (await db.execute(
        select(MessageReaction).where(MessageReaction.message_id == message_id)
    )).scalars().all()
    return [
        {"emoji": r.emoji, "user_id": str(r.user_id), "created_at": r.created_at.isoformat()}
        for r in rows
    ]


async def _get_user_by_id(db: AsyncSession, uid: str | uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


async def _get_match_partner_ids(uid: str) -> list[str]:
    """Return all matched partner IDs for a user (raw SQL, no ORM model needed)."""
    from sqlalchemy import text as sa_text
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            sa_text("""
                SELECT CASE
                    WHEN user1_id = CAST(:uid AS uuid) THEN user2_id
                    ELSE user1_id
                END AS partner_id
                FROM matches
                WHERE user1_id = CAST(:uid AS uuid) OR user2_id = CAST(:uid AS uuid)
            """).bindparams(uid=uid)
        )).fetchall()
    return [str(r[0]) for r in rows]


async def _broadcast_presence(uid: str, online: bool) -> None:
    """Fan-out a presence event to all matched partners of *uid* via notify_manager."""
    import asyncio as _asyncio
    try:
        partner_ids = await _get_match_partner_ids(uid)
    except _asyncio.CancelledError:
        # Server is shutting down — let the cancellation propagate cleanly.
        raise
    except BaseException as exc:
        _log.debug("_broadcast_presence | DB error for user=%s: %s", uid[:8], exc)
        return
    payload = {"type": "presence", "user_id": uid, "online": online}
    for pid in partner_ids:
        await notify_manager.send_to(pid, payload)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@ws_router.websocket("/ws/chat/{other_user_id}")
async def websocket_chat(
    websocket: WebSocket,
    other_user_id: str,
    token: str = Query(..., description="JWT access token"),
):
    """
    Real-time chat WebSocket.

    Client connects:
      ws://host/api/v1/ws/chat/{other_user_id}?token=<jwt>

    Client sends JSON:
      {"type": "message", "content": "Hello!", "msg_type": "text", "extra": null}
      {"type": "read"}                         ← mark messages as read
      {"type": "typing", "is_typing": true}    ← typing indicator

    Server pushes JSON:
      {"type": "message",  ...message_fields}
      {"type": "typing",   "sender_id": "...", "is_typing": true}
      {"type": "read",     "reader_id": "..."}
      {"type": "error",    "detail": "..."}
    """
    # Authenticate via JWT query param
    async with AsyncSessionLocal() as db:
        try:
            from app.core.security import decode_access_token
            payload = decode_access_token(token)
            user_id = payload.get("sub")
            if not user_id:
                raise ValueError("missing sub")
        except Exception:
            await websocket.close(code=4001)
            return

        current_user = await _get_user_by_id(db, user_id)
        other_user   = await _get_user_by_id(db, other_user_id)

        if not current_user or not other_user:
            await websocket.close(code=4004)
            return

        # Recipient requires face-verified senders — close before accepting
        if other_user.require_verified_to_chat and current_user.verification_status != "verified":
            await websocket.accept()
            await websocket.send_text(json.dumps({
                "type": "error",
                "detail": "This person only accepts messages from verified users. Verify your photo to send a message.",
                "code": "verification_required",
            }))
            await websocket.close(code=4003)
            return

        uid_me    = str(current_user.id)
        uid_other = str(other_user.id)
        room_id   = Message.make_room_id(current_user.id, other_user.id)

    await websocket.accept()
    manager.connect(uid_me, websocket)

    # Broadcast presence to the other user so their header updates live
    await notify_manager.send_to(uid_other, {"type": "presence", "user_id": uid_me, "online": True})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload_in: dict = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "detail": "invalid JSON"}))
                continue

            msg_type_ws = payload_in.get("type", "message")

            # ── Typing indicator ──────────────────────────────────────────────
            if msg_type_ws == "typing":
                await manager.send_to(uid_other, {
                    "type": "typing",
                    "sender_id": uid_me,
                    "is_typing": bool(payload_in.get("is_typing", False)),
                })
                continue

            # ── Read receipt ──────────────────────────────────────────────────
            if msg_type_ws == "read":
                now_utc = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Message)
                        .where(
                            Message.room_id == room_id,
                            Message.sender_id == other_user.id,
                            Message.receiver_id == current_user.id,
                            Message.is_read == False,  # noqa: E712
                        )
                        .values(is_read=True, read_at=now_utc)
                    )
                    await db.commit()
                await manager.send_to(uid_other, {
                    "type": "read", "reader_id": uid_me,
                    "read_at": now_utc.isoformat(),
                })
                continue

            # ── Truth-or-Dare game messages ───────────────────────────────────
            if msg_type_ws in ("tod_invite", "tod_choice", "tod_accept", "tod_answer", "tod_next", "tod_skip"):
                from app.models.tod_round import TodRound
                from datetime import timedelta

                game_meta = payload_in.get("extra") or {}
                game_meta["sender_id"] = uid_me
                game_content = payload_in.get("content", "🎲 Truth or Dare")
                now_utc = datetime.now(timezone.utc)
                game_ttl = timedelta(hours=12)

                async with AsyncSessionLocal() as db:
                    # ── 1. Persist the chat message (existing behaviour) ──────
                    msg = Message(
                        room_id=room_id,
                        sender_id=current_user.id,
                        receiver_id=other_user.id,
                        content=game_content,
                        msg_type=msg_type_ws,
                        extra=game_meta,
                        is_read=False,
                        created_at=now_utc,
                    )
                    db.add(msg)
                    await db.flush()          # gives msg.id without committing yet

                    # ── helpers ───────────────────────────────────────────────
                    async def _latest_tod_round(*statuses: str) -> "TodRound | None":
                        """Fallback: newest round in this room whose status is in *statuses*."""
                        return (await db.execute(
                            select(TodRound)
                            .where(TodRound.room_id == room_id, TodRound.status.in_(list(statuses)))
                            .order_by(TodRound.created_at.desc())
                            .limit(1)
                        )).scalar_one_or_none()

                    async def _tod_by_invite(raw_id: str | None) -> "TodRound | None":
                        if not raw_id:
                            return None
                        try:
                            return (await db.execute(
                                select(TodRound).where(TodRound.invite_msg_id == uuid.UUID(str(raw_id)))
                            )).scalar_one_or_none()
                        except (ValueError, TypeError):
                            return None

                    async def _tod_by_question(raw_id: str | None) -> "TodRound | None":
                        if not raw_id:
                            return None
                        try:
                            return (await db.execute(
                                select(TodRound).where(TodRound.question_msg_id == uuid.UUID(str(raw_id)))
                            )).scalar_one_or_none()
                        except (ValueError, TypeError):
                            return None

                    # ── 2. Create / update the dedicated tod_rounds row ───────
                    if msg_type_ws == "tod_invite":
                        # Expire any active rounds in this room (started within 12 h)
                        active_cutoff = now_utc - game_ttl
                        active_rounds = (await db.execute(
                            select(TodRound).where(
                                TodRound.room_id == room_id,
                                TodRound.status.notin_(["answered", "skipped", "expired"]),
                                TodRound.created_at >= active_cutoff,
                            )
                        )).scalars().all()
                        for old_round in active_rounds:
                            old_round.status = "expired"
                            old_round.updated_at = now_utc

                        # preSelectedChoice — sender already picked the type for the partner.
                        # Used when the answerer immediately challenges back without a separate tod_choice step.
                        pre_choice = game_meta.get("preSelectedChoice")

                        tod_row = TodRound(
                            room_id=room_id,
                            invite_msg_id=msg.id,
                            sender_id=current_user.id,
                            receiver_id=other_user.id,
                            choice=pre_choice,
                            status="choice_made" if pre_choice else "invited",
                            created_at=now_utc,
                            updated_at=now_utc,
                        )
                        db.add(tod_row)

                    elif msg_type_ws == "tod_skip":
                        # Receiver skipped — mark the round as skipped
                        tod_row = (
                            await _tod_by_question(game_meta.get("turnMsgId"))
                            or await _latest_tod_round("question_sent")
                        )
                        if tod_row:
                            tod_row.status       = "skipped"
                            tod_row.updated_at   = now_utc
                            tod_row.completed_at = now_utc

                    elif msg_type_ws == "tod_choice":
                        # Partner chose Truth or Dare — update the open round
                        tod_row = (
                            await _tod_by_invite(game_meta.get("inviteId"))
                            or await _latest_tod_round("invited")
                        )
                        if tod_row:
                            tod_row.choice = game_meta.get("choice")
                            tod_row.status = "choice_made"
                            tod_row.updated_at = now_utc

                    elif msg_type_ws == "tod_next":
                        # Sender picked and sent the actual question card
                        tod_row = (
                            await _tod_by_invite(game_meta.get("inviteId"))
                            or await _latest_tod_round("invited", "choice_made")
                        )
                        if tod_row:
                            tod_row.question          = game_meta.get("question")
                            tod_row.question_emoji    = game_meta.get("emoji")
                            tod_row.question_category = game_meta.get("category")
                            tod_row.question_msg_id   = msg.id
                            tod_row.status            = "question_sent"
                            tod_row.updated_at        = now_utc

                    elif msg_type_ws == "tod_answer":
                        # Receiver answered — complete the round
                        turn_id_raw = game_meta.get("turnMsgId") or game_meta.get("inviteId")
                        tod_row = (
                            await _tod_by_question(turn_id_raw)
                            or await _tod_by_invite(turn_id_raw)
                            or await _latest_tod_round("question_sent")
                        )
                        if tod_row:
                            tod_row.answer        = game_content
                            tod_row.answer_msg_id = msg.id
                            tod_row.status        = "answered"
                            tod_row.updated_at    = now_utc
                            tod_row.completed_at  = now_utc

                    await db.commit()
                    await db.refresh(msg)
                    msg_dict = _msg_to_dict(msg)
                    fresh_other = await _get_user_by_id(db, uid_other)
                    other_push_token = fresh_other.push_token if fresh_other else None

                outgoing = {"type": "message", **msg_dict}
                await websocket.send_text(json.dumps(outgoing))
                delivered = await manager.send_to(uid_other, outgoing)

                notify_payload = {"type": "new_message", **msg_dict}
                await notify_manager.send_to(uid_me, notify_payload)
                if not delivered:
                    await notify_manager.send_to(uid_other, notify_payload)
                    sender_name = current_user.full_name or "Someone"
                    _choice_label = (game_meta.get("choice") or "truth").capitalize()
                    tod_push_map = {
                        "tod_invite":  (f"🎲 {sender_name} wants to play!", "Truth or Dare — tap to join!"),
                        "tod_choice":  (f"🎲 {sender_name} chose {_choice_label}!", f"Send them a {_choice_label} question now →"),
                        "tod_accept":  (f"🎲 {sender_name} accepted!", "They're ready to play Truth or Dare"),
                        "tod_answer":  (f"🎲 {sender_name} answered!", "Tap to see their answer"),
                        "tod_next":    (f"🎲 {sender_name} sent a question!", "Truth or Dare — your turn"),
                        "tod_skip":    (f"↩ {sender_name} skipped", "They'll need a new question — send another!"),
                    }
                    push_title, push_body = tod_push_map.get(msg_type_ws, (f"🎲 {sender_name}", game_content[:80]))
                    await send_push_notification(
                        other_push_token,
                        title=push_title,
                        body=push_body,
                        data={"type": "chat", "room_id": room_id, "other_user_id": uid_me},
                    )
                continue

            # ── Game response (receiver answered a bubble — update in-place) ───
            if msg_type_ws == "game_response":
                ref_msg_id = payload_in.get("ref_msg_id")
                response_extra = payload_in.get("extra") or {}
                response_extra["responder_id"] = uid_me
                relay = {
                    "type": "game_response",
                    "ref_msg_id": ref_msg_id,
                    "extra": response_extra,
                }
                delivered_relay = await manager.send_to(uid_other, relay)
                # Also push via notify channel so other screens (ChatsPage) can update
                if not delivered_relay:
                    await notify_manager.send_to(uid_other, relay)
                    # Push notification so the original sender knows their partner responded
                    async with AsyncSessionLocal() as db:
                        fresh_other = await _get_user_by_id(db, uid_other)
                        other_push_token = fresh_other.push_token if fresh_other else None
                    responder_name = current_user.full_name or "Someone"
                    await send_push_notification(
                        other_push_token,
                        title=f"🎮 {responder_name} responded!",
                        body="They answered your game card — tap to see!",
                        data={"type": "chat", "room_id": room_id, "other_user_id": uid_me},
                    )
                continue

            # ── Mini-game messages ────────────────────────────────────────────
            MINI_GAME_TYPES = {
                "game_wyr", "game_nhi", "game_hot",
                "game_quiz", "game_date", "game_emoji",
            }
            if msg_type_ws in MINI_GAME_TYPES:
                game_meta = payload_in.get("extra") or {}
                game_meta["sender_id"] = uid_me
                game_content = payload_in.get("content", "🎮 Game")

                GAME_LABELS = {
                    "game_wyr":   "🤷 Would You Rather",
                    "game_nhi":   "🍹 Never Have I Ever",
                    "game_hot":   "🔥 Hot Takes",
                    "game_quiz":  "💘 Compatibility Quiz",
                    "game_date":  "🗓️ Build a Date",
                    "game_emoji": "😂 Emoji Story",
                }
                push_title = f"{GAME_LABELS.get(msg_type_ws, '🎮 Game')} from {current_user.full_name or 'Someone'}"

                async with AsyncSessionLocal() as db:
                    msg = Message(
                        room_id=room_id,
                        sender_id=current_user.id,
                        receiver_id=other_user.id,
                        content=game_content,
                        msg_type=msg_type_ws,
                        extra=game_meta,
                        is_read=False,
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(msg)
                    await db.commit()
                    await db.refresh(msg)
                    msg_dict = _msg_to_dict(msg)
                    fresh_other = await _get_user_by_id(db, uid_other)
                    other_push_token = fresh_other.push_token if fresh_other else None

                outgoing = {"type": "message", **msg_dict}
                await websocket.send_text(json.dumps(outgoing))
                delivered = await manager.send_to(uid_other, outgoing)

                notify_payload = {"type": "new_message", **msg_dict}
                await notify_manager.send_to(uid_me, notify_payload)
                if not delivered:
                    await notify_manager.send_to(uid_other, notify_payload)
                    await send_push_notification(
                        other_push_token,
                        title=push_title,
                        body="Tap to play!",
                        data={"type": "chat", "room_id": room_id, "other_user_id": uid_me},
                    )
                continue


            # ── Call signalling (relay via notify_manager for global delivery) ─
            if msg_type_ws in ("call_invite", "call_accept", "call_decline", "call_end"):
                signal: dict[str, Any] = {"type": msg_type_ws, "from": uid_me}
                if msg_type_ws == "call_invite":
                    caller_name  = current_user.full_name or "Someone"
                    caller_image = (current_user.photos or [None])[0] if current_user.photos else None
                    signal.update({
                        "caller_name":  caller_name,
                        "caller_image": caller_image,
                    })
                    await send_push_notification(
                        other_user.push_token,
                        title=f"📞 {caller_name} is calling…",
                        body="Tap to answer",
                        data={
                            "type":         "call",
                            "from":         uid_me,
                            "caller_name":  caller_name,
                            "caller_image": caller_image,
                            "room_id":      room_id,
                        },
                    )
                elif msg_type_ws == "call_end":
                    signal["duration"] = int(payload_in.get("duration", 0))
                # Deliver to the other user on whichever socket they have open
                await manager.send_to(uid_other, signal)
                await notify_manager.send_to(uid_other, signal)
                # Persist a call record on end / decline
                # call_decline via chat-WS: only relay, no record (same as notify-WS path)
                # call_end via chat-WS: create record with deduplication
                if msg_type_ws == "call_end":
                    dur       = int(payload_in.get("duration", 0))
                    call_type = "ended" if dur > 0 else "missed"
                    async with AsyncSessionLocal() as db:
                        from datetime import timedelta
                        from sqlalchemy import select as sa_select
                        cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
                        existing = (await db.execute(
                            sa_select(Message).where(
                                Message.room_id == room_id,
                                Message.msg_type == "call",
                                Message.created_at > cutoff,
                            )
                        )).scalar_one_or_none()
                        if existing is None:
                            call_msg = Message(
                                room_id=room_id,
                                sender_id=current_user.id,
                                receiver_id=other_user.id,
                                content="",
                                msg_type="call",
                                extra={"call_type": call_type, "duration": dur},
                                is_read=False,
                                created_at=datetime.now(timezone.utc),
                            )
                            db.add(call_msg)
                            await db.commit()
                            await db.refresh(call_msg)
                            rec = {"type": "call_record", **_msg_to_dict(call_msg)}
                            await websocket.send_text(json.dumps(rec))
                            await manager.send_to(uid_other, rec)
                            await notify_manager.send_to(uid_other, rec)
                            await notify_manager.send_to(uid_me, rec)
                continue

            content  = str(payload_in.get("content", "")).strip()
            msg_type = payload_in.get("msg_type", "text")
            metadata = payload_in.get("metadata")

            if not content:
                await websocket.send_text(json.dumps({"type": "error", "detail": "empty content"}))
                continue

            # ── Content restriction (text / answer messages only) ─────────────
            if msg_type in ("text", "answer", "card"):
                from app.utils.content_filter import check_content
                violation = check_content(content)
                if violation:
                    await websocket.send_text(json.dumps({"type": "restricted", "detail": violation}))
                    continue

            async with AsyncSessionLocal() as db:
                msg = Message(
                    room_id=room_id,
                    sender_id=current_user.id,
                    receiver_id=other_user.id,
                    content=content,
                    msg_type=msg_type,
                    extra=metadata,
                    is_read=False,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)
                msg_dict = _msg_to_dict(msg)

                # Re-fetch push token inside this session scope
                fresh_other = await _get_user_by_id(db, uid_other)
                other_push_token = fresh_other.push_token if fresh_other else None

            # Broadcast to sender (echo) and receiver
            outgoing = {"type": "message", **msg_dict}
            await websocket.send_text(json.dumps(outgoing))
            delivered = await manager.send_to(uid_other, outgoing)

            # Always notify both parties via notify_manager so ChatsPage stays live.
            # Sender gets the echo so their own conversation list updates in real-time.
            # Recipient gets it so their list updates even when not inside the chat room.
            notify_payload = {"type": "new_message", **msg_dict}
            await notify_manager.send_to(uid_me,    notify_payload)
            if not delivered:
                await notify_manager.send_to(uid_other, notify_payload)

            # Push notification if the recipient is offline
            if not delivered:
                sender_name = current_user.full_name or "Someone"
                # Friendly preview — don't expose raw URLs in notifications
                if msg_type == "image":
                    preview = "📷 Sent a photo"
                elif msg_type == "voice":
                    preview = "🎙️ Sent a voice message"
                elif msg_type == "card":
                    preview = "🃏 Sent a card"
                elif msg_type in ("game_wyr", "wyr"):
                    preview = "🤷 Sent a Would You Rather"
                elif msg_type in ("game_nhi", "nhi"):
                    preview = "🍹 Sent Never Have I Ever"
                elif msg_type in ("game_hot", "hot_takes"):
                    preview = "🔥 Sent a Hot Take"
                elif msg_type in ("game_quiz",):
                    preview = "💘 Sent a Compatibility Quiz"
                elif msg_type in ("game_date",):
                    preview = "🗓️ Sent Build a Date"
                elif msg_type in ("game_emoji",):
                    preview = "😂 Sent an Emoji Story"
                elif msg_type in ("question_cards",):
                    preview = "🎮 Sent a game card"
                elif msg_type == "tod_invite":
                    preview = "🎲 Wants to play Truth or Dare"
                elif msg_type == "tod_answer":
                    preview = "🎲 Answered your Truth or Dare"
                elif msg_type == "call":
                    preview = "📞 Missed call"
                else:
                    preview = content[:80]
                await notify_user(
                    fresh_other, "chat",
                    title=f"💬 {sender_name}",
                    body=preview,
                    data={
                        "type":           "chat",
                        "room_id":        room_id,
                        "sender_id":      uid_me,
                        "sender_name":    sender_name,
                        "other_user_id":  uid_me,
                    },
                )

    except (WebSocketDisconnect, OSError):
        pass
    except Exception as exc:
        _log.debug("ws:chat | connection dropped for user=%s: %s", uid_me[:8], exc)
    finally:
        manager.disconnect(uid_me)
        # Broadcast offline presence
        await notify_manager.send_to(uid_other, {"type": "presence", "user_id": uid_me, "online": False})


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/chat/conversations", summary="List conversations with latest message per match")
async def get_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Returns a list of conversations (one per matched user) with the latest
    message preview, unread count, and basic partner profile info.
    """
    from sqlalchemy import text

    uid = str(current_user.id)

    # Get all matches for this user
    matches_rows = await db.execute(
        text("""
            SELECT
                CASE WHEN user1_id = CAST(:uid AS uuid) THEN user2_id ELSE user1_id END AS partner_id,
                matched_at
            FROM matches
            WHERE user1_id = CAST(:uid AS uuid) OR user2_id = CAST(:uid AS uuid)
            ORDER BY matched_at DESC
        """).bindparams(uid=uid)
    )
    match_rows = matches_rows.fetchall()

    if not match_rows:
        return {"conversations": [], "total": 0}

    partner_ids = [str(r[0]) for r in match_rows]

    # Fetch partner profiles
    partners_result = await db.execute(
        select(User).where(User.id.in_(partner_ids))
    )
    partners: dict[str, User] = {str(u.id): u for u in partners_result.scalars().all()}

    conversations = []
    for partner_id in partner_ids:
        partner = partners.get(partner_id)
        if not partner:
            continue

        room_id = Message.make_room_id(current_user.id, partner.id)

        # Latest message in this room
        latest_result = await db.execute(
            select(Message)
            .where(Message.room_id == room_id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        latest_msg = latest_result.scalar_one_or_none()

        # Unread count (messages FROM partner that I haven't read)
        unread_result = await db.execute(
            select(Message)
            .where(
                Message.room_id == room_id,
                Message.sender_id == partner.id,
                Message.is_read == False,  # noqa: E712
            )
        )
        unread_count = len(unread_result.scalars().all())

        conversations.append({
            "partner_id":     str(partner.id),
            "partner_name":   partner.full_name or "Unknown",
            "partner_image":  (partner.photos or [None])[0],
            "room_id":        room_id,
            "last_message":   _msg_to_dict(latest_msg) if latest_msg else None,
            "unread_count":   unread_count,
            "is_online":      notify_manager.is_online(str(partner.id)),
        })

    return {"conversations": conversations, "total": len(conversations)}


@router.delete("/chat/messages/{message_id}", summary="Unsend (delete) a message you sent")
async def unsend_message(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Permanently deletes a message. Only the original sender may unsend it.
    Notifies the other party via the notify WebSocket so their UI hides it instantly.
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    msg: Message | None = await db.get(Message, msg_uuid)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only unsend your own messages")

    room_id     = msg.room_id
    receiver_id = str(msg.receiver_id)

    await db.delete(msg)
    await db.commit()

    # Notify the receiver so their UI removes the message in real-time
    await notify_manager.send_to(receiver_id, {
        "type":       "message_deleted",
        "message_id": message_id,
        "room_id":    room_id,
    })

    return {"success": True, "message_id": message_id}


@router.get("/chat/{other_user_id}/messages", summary="Paginated message history for a conversation")
async def get_messages(
    other_user_id: str,
    limit:  int = Query(50,  ge=1, le=200),
    before: str = Query(None, description="ISO timestamp — fetch messages older than this"),
    after:  str = Query(None, description="ISO timestamp — fetch messages newer than this (catch-up)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    try:
        other_uuid = uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    room_id = Message.make_room_id(current_user.id, other_uuid)

    query = (
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
            query = query.where(Message.created_at < before_dt)
        except ValueError:
            pass
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
            query = query.where(Message.created_at > after_dt)
        except ValueError:
            pass

    result = await db.execute(query)
    messages = list(reversed(result.scalars().all()))

    # Batch-fetch all reactions for this page in a single query (avoids N+1)
    reactions_by_msg: dict[uuid.UUID, list[dict]] = {}
    if messages:
        msg_ids = [m.id for m in messages]
        rxn_rows = (await db.execute(
            select(MessageReaction).where(MessageReaction.message_id.in_(msg_ids))
        )).scalars().all()
        for r in rxn_rows:
            reactions_by_msg.setdefault(r.message_id, []).append({
                "emoji":      r.emoji,
                "user_id":    str(r.user_id),
                "created_at": r.created_at.isoformat(),
            })

    return {
        "messages": [_msg_to_dict(m, reactions_by_msg.get(m.id, [])) for m in messages],
        "total":    len(messages),
        "has_more": len(messages) == limit,
    }


@router.post("/chat/{other_user_id}/messages", status_code=201, summary="Send a message (REST fallback)")
async def send_message(
    other_user_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    try:
        other_uuid = uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    other_user = await _get_user_by_id(db, other_uuid)
    if not other_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Recipient requires face-verified senders only
    if other_user.require_verified_to_chat and current_user.verification_status != "verified":
        raise HTTPException(
            status_code=403,
            detail="This person only accepts messages from verified users. Verify your photo to send a message.",
        )

    content  = str(body.get("content", "")).strip()
    msg_type = body.get("msg_type", "text")
    metadata = body.get("metadata")

    if not content:
        raise HTTPException(status_code=422, detail="content is required")

    room_id = Message.make_room_id(current_user.id, other_uuid)
    msg = Message(
        room_id=room_id,
        sender_id=current_user.id,
        receiver_id=other_uuid,
        content=content,
        msg_type=msg_type,
        extra=metadata,
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    msg_dict = _msg_to_dict(msg)
    outgoing = {"type": "message", **msg_dict}

    # Try WebSocket delivery to recipient's chat room connection
    delivered = await manager.send_to(str(other_uuid), outgoing)
    # Also echo to sender if they are connected
    await manager.send_to(str(current_user.id), outgoing)

    # Notify both parties via notify_manager so ChatsPage stays live on both sides
    notify_payload = {"type": "new_message", **msg_dict}
    await notify_manager.send_to(str(current_user.id), notify_payload)
    if not delivered:
        await notify_manager.send_to(str(other_uuid), notify_payload)

    if not delivered:
        sender_name = current_user.full_name or "Someone"
        await notify_user(
            other_user, "chat",
            title=f"💬 {sender_name}",
            body=content[:80],
            data={
                "type":          "chat",
                "room_id":       room_id,
                "sender_id":     str(current_user.id),
                "sender_name":   sender_name,
                "other_user_id": str(current_user.id),
            },
        )

    return msg_dict


@router.put("/chat/{other_user_id}/read", summary="Mark messages from other_user as read")
async def mark_read(
    other_user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    try:
        other_uuid = uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    room_id = Message.make_room_id(current_user.id, other_uuid)

    now_utc = datetime.now(timezone.utc)
    await db.execute(
        update(Message)
        .where(
            Message.room_id == room_id,
            Message.sender_id == other_uuid,
            Message.receiver_id == current_user.id,
            Message.is_read == False,  # noqa: E712
        )
        .values(is_read=True, read_at=now_utc)
    )
    await db.commit()

    await manager.send_to(str(other_uuid), {
        "type": "read",
        "reader_id": str(current_user.id),
        "read_at": now_utc.isoformat(),
    })

    return {"ok": True}


# ── React to a message ───────────────────────────────────────────────────────

class ReactBody(BaseModel):
    emoji: str


@router.post("/chat/messages/{message_id}/react", summary="Add or toggle an emoji reaction")
async def react_to_message(
    message_id: str,
    body: ReactBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Toggle a reaction: if the user has already reacted with this emoji, the
    reaction is removed (un-react). Otherwise it is added.
    The partner is notified in real-time via the notify WebSocket.
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    emoji = body.emoji.strip()[:16]
    if not emoji:
        raise HTTPException(status_code=422, detail="emoji is required")

    msg: Message | None = await db.get(Message, msg_uuid)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    uid_me    = str(current_user.id)
    # Partner is whoever is in the conversation but isn't us
    uid_other = str(msg.receiver_id) if str(msg.sender_id) == uid_me else str(msg.sender_id)

    # Check for existing reaction
    existing = (await db.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == msg_uuid,
            MessageReaction.user_id    == current_user.id,
            MessageReaction.emoji      == emoji,
        )
    )).scalar_one_or_none()

    if existing:
        await db.delete(existing)
        action = "removed"
    else:
        db.add(MessageReaction(
            message_id=msg_uuid,
            user_id=current_user.id,
            emoji=emoji,
        ))
        action = "added"

    await db.commit()

    # Re-fetch full reaction list and relay to both parties
    reactions = await _get_reactions(db, msg_uuid)
    relay = {
        "type":       "reaction_update",
        "message_id": message_id,
        "reactions":  reactions,
    }
    await notify_manager.send_to(uid_me,    relay)
    await notify_manager.send_to(uid_other, relay)
    await manager.send_to(uid_other, relay)

    return {"action": action, "reactions": reactions}


# ── Edit a message ────────────────────────────────────────────────────────────

class EditBody(BaseModel):
    content: str


@router.patch("/chat/messages/{message_id}", summary="Edit a sent message")
async def edit_message(
    message_id: str,
    body: EditBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Sender can edit the content of a text message they sent.
    Only text messages can be edited; media and game messages are rejected.
    The partner is notified in real-time.
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    msg: Message | None = await db.get(Message, msg_uuid)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own messages")
    if msg.msg_type not in ("text", "answer"):
        raise HTTPException(status_code=422, detail="Only text messages can be edited")

    new_content = body.content.strip()
    if not new_content:
        raise HTTPException(status_code=422, detail="content cannot be empty")

    from app.utils.content_filter import check_content
    violation = check_content(new_content)
    if violation:
        raise HTTPException(status_code=422, detail=violation)

    msg.content   = new_content
    msg.edited_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)

    uid_other = str(msg.receiver_id)
    relay = {
        "type":       "message_edited",
        "message_id": message_id,
        "content":    new_content,
        "edited_at":  msg.edited_at.isoformat(),
    }
    await notify_manager.send_to(str(current_user.id), relay)
    await notify_manager.send_to(uid_other, relay)
    await manager.send_to(uid_other, relay)

    return {"ok": True, "edited_at": msg.edited_at.isoformat()}


# ── Message info (delivery + read times) ─────────────────────────────────────

@router.get("/chat/messages/{message_id}/info", summary="Get delivery and read timestamps for a message")
async def message_info(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Returns sent_at, delivered_at (same as sent for WebSocket delivery),
    and read_at for a message the current user sent or received.
    """
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    msg: Message | None = await db.get(Message, msg_uuid)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    uid_me = str(current_user.id)
    if str(msg.sender_id) != uid_me and str(msg.receiver_id) != uid_me:
        raise HTTPException(status_code=403, detail="Access denied")

    reactions = await _get_reactions(db, msg_uuid)

    return {
        "message_id":   message_id,
        "sent_at":      msg.created_at.isoformat(),
        "edited_at":    msg.edited_at.isoformat() if msg.edited_at else None,
        "is_read":      msg.is_read,
        "read_at":      msg.read_at.isoformat() if msg.read_at else None,
        "reactions":    reactions,
    }


# ── Unmatch ───────────────────────────────────────────────────────────────────

class UnmatchBody(BaseModel):
    reason: str | None = None
    custom_reason: str | None = None


@router.post("/chat/{other_user_id}/unmatch", summary="Unmatch and remove a conversation")
async def unmatch(
    other_user_id: str,
    body: UnmatchBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Removes the match between the current user and other_user_id, deletes all
    messages in the shared room, and pushes a real-time 'unmatch' event to the
    other party via the notify WebSocket.
    """
    from sqlalchemy import text, delete as sa_delete

    try:
        other_uuid = uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    uid = str(current_user.id)
    other_str = str(other_uuid)

    # Delete match row (stable ordering — smaller uuid is always user1_id)
    await db.execute(
        text("""
            DELETE FROM matches
            WHERE (user1_id = CAST(:a AS uuid) AND user2_id = CAST(:b AS uuid))
               OR (user1_id = CAST(:b AS uuid) AND user2_id = CAST(:a AS uuid))
        """).bindparams(a=uid, b=other_str)
    )

    # Delete all messages in the shared room
    room_id = Message.make_room_id(current_user.id, other_uuid)
    await db.execute(sa_delete(Message).where(Message.room_id == room_id))

    # Record a "left" (dislike) swipe from the unmatching user in all feed modes
    # so the other person will not appear in their feed again.
    for mode in ("date", "work"):
        await db.execute(
            text("""
                INSERT INTO swipes (swiper_id, swiped_id, direction, mode)
                VALUES (CAST(:swiper AS uuid), CAST(:swiped AS uuid), 'left', :mode)
                ON CONFLICT (swiper_id, swiped_id, mode) DO UPDATE SET direction = 'left'
            """).bindparams(swiper=uid, swiped=other_str, mode=mode)
        )

    await db.commit()

    # Notify the other user in real time
    await notify_manager.send_to(
        other_str,
        {"type": "unmatch", "user_id": uid},
    )

    return {"ok": True}


# ── Block ─────────────────────────────────────────────────────────────────────

@router.post("/chat/{other_user_id}/block", summary="Block a user and remove the match")
async def block_user(
    other_user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Blocks other_user_id:
      • Removes any existing match + all shared messages (same as unmatch)
      • Inserts a 'block' direction swipe for every mode so neither profile
        appears in the other's feed again
    """
    from sqlalchemy import text, delete as sa_delete

    try:
        other_uuid = uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    uid = str(current_user.id)
    other_str = str(other_uuid)

    # Remove match (if any)
    await db.execute(
        text("""
            DELETE FROM matches
            WHERE (user1_id = CAST(:a AS uuid) AND user2_id = CAST(:b AS uuid))
               OR (user1_id = CAST(:b AS uuid) AND user2_id = CAST(:a AS uuid))
        """).bindparams(a=uid, b=other_str)
    )

    # Delete shared messages
    room_id = Message.make_room_id(current_user.id, other_uuid)
    await db.execute(sa_delete(Message).where(Message.room_id == room_id))

    # Insert block swipes for all feed modes so neither appears in the other's feed
    for mode in ("date", "work"):
        await db.execute(
            text("""
                INSERT INTO swipes (swiper_id, swiped_id, direction, mode)
                VALUES (CAST(:swiper AS uuid), CAST(:swiped AS uuid), 'block', :mode)
                ON CONFLICT (swiper_id, swiped_id, mode) DO UPDATE SET direction = 'block'
            """).bindparams(swiper=uid, swiped=other_str, mode=mode)
        )
        # Reverse so the blocked user also won't see the blocker
        await db.execute(
            text("""
                INSERT INTO swipes (swiper_id, swiped_id, direction, mode)
                VALUES (CAST(:swiper AS uuid), CAST(:swiped AS uuid), 'block', :mode)
                ON CONFLICT (swiper_id, swiped_id, mode) DO UPDATE SET direction = 'block'
            """).bindparams(swiper=other_str, swiped=uid, mode=mode)
        )

    await db.commit()

    return {"ok": True}


# ── Report ────────────────────────────────────────────────────────────────────

class ReportBody(BaseModel):
    reason: str = "user_report"
    custom_reason: str | None = None


@router.post("/chat/{other_user_id}/report", summary="Report a user")
async def report_user(
    other_user_id: str,
    body: ReportBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Stores a user report. The report is reviewed by the trust & safety team.
    Does NOT automatically remove the match — use /block or /unmatch for that.
    """
    try:
        other_uuid = uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    report = UserReport(
        reporter_id=current_user.id,
        reported_id=other_uuid,
        reason=body.reason,
        custom_reason=body.custom_reason,
    )
    db.add(report)
    await db.commit()

    return {"ok": True}


# ── Truth-or-Dare game history ────────────────────────────────────────────────

@router.get("/chat/{other_user_id}/tod-history", summary="Paginated Truth-or-Dare round history")
async def get_tod_history(
    other_user_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """
    Returns all Truth-or-Dare rounds played in a conversation, newest first.
    Each row contains the full lifecycle: invite → choice → question → answer.
    """
    from app.models.tod_round import TodRound

    try:
        uuid.UUID(other_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    room_id = Message.make_room_id(current_user.id, uuid.UUID(other_user_id))

    result = await db.execute(
        select(TodRound)
        .where(TodRound.room_id == room_id)
        .order_by(TodRound.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = result.scalars().all()

    def _row(r: TodRound) -> dict:
        return {
            "id":                 str(r.id),
            "room_id":            r.room_id,
            "invite_msg_id":      str(r.invite_msg_id) if r.invite_msg_id else None,
            "sender_id":          str(r.sender_id),
            "receiver_id":        str(r.receiver_id),
            "choice":             r.choice,
            "question":           r.question,
            "question_emoji":     r.question_emoji,
            "question_category":  r.question_category,
            "question_msg_id":    str(r.question_msg_id) if r.question_msg_id else None,
            "answer":             r.answer,
            "answer_msg_id":      str(r.answer_msg_id) if r.answer_msg_id else None,
            "status":             r.status,
            "created_at":         r.created_at.isoformat(),
            "updated_at":         r.updated_at.isoformat(),
            "completed_at":       r.completed_at.isoformat() if r.completed_at else None,
        }

    return {"rounds": [_row(r) for r in rows], "total": len(rows)}
