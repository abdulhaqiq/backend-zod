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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.push import send_push_notification
from app.db.session import AsyncSessionLocal, get_db
from app.models.message import Message
from app.models.user import User

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _msg_to_dict(m: Message) -> dict[str, Any]:
    return {
        "id":          str(m.id),
        "room_id":     m.room_id,
        "sender_id":   str(m.sender_id),
        "receiver_id": str(m.receiver_id),
        "content":     m.content,
        "msg_type":    m.msg_type,
        "metadata": m.extra,
        "is_read":     m.is_read,
        "created_at":  m.created_at.isoformat(),
    }


async def _get_user_by_id(db: AsyncSession, uid: str | uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


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
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(Message)
                        .where(
                            Message.room_id == room_id,
                            Message.sender_id == other_user.id,
                            Message.receiver_id == current_user.id,
                            Message.is_read == False,  # noqa: E712
                        )
                        .values(is_read=True)
                    )
                    await db.commit()
                await manager.send_to(uid_other, {"type": "read", "reader_id": uid_me})
                continue

            # ── Truth-or-Dare game messages ───────────────────────────────────
            if msg_type_ws in ("tod_invite", "tod_accept", "tod_answer", "tod_next"):
                game_meta = payload_in.get("extra") or {}
                game_meta["sender_id"] = uid_me

                # Map ws type → db msg_type
                db_msg_type = msg_type_ws  # tod_invite | tod_accept | tod_answer | tod_next
                game_content = payload_in.get("content", "🎲 Truth or Dare")

                async with AsyncSessionLocal() as db:
                    msg = Message(
                        room_id=room_id,
                        sender_id=current_user.id,
                        receiver_id=other_user.id,
                        content=game_content,
                        msg_type=db_msg_type,
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

                if not delivered:
                    await notify_manager.send_to(uid_other, {"type": "new_message", **msg_dict})
                    if msg_type_ws == "tod_invite":
                        await send_push_notification(
                            other_push_token,
                            title=f"🎲 {current_user.full_name or 'Someone'} wants to play!",
                            body="Truth or Dare — tap to join!",
                            data={"type": "chat", "room_id": room_id, "other_user_id": uid_me},
                        )
                continue

            # ── Game response (receiver answered a bubble — update in-place) ───
            if msg_type_ws == "game_response":
                ref_msg_id = payload_in.get("ref_msg_id")
                response_extra = payload_in.get("extra") or {}
                response_extra["responder_id"] = uid_me
                # Just relay to the other user so their bubble updates in-place too
                relay = {
                    "type": "game_response",
                    "ref_msg_id": ref_msg_id,
                    "extra": response_extra,
                }
                await manager.send_to(uid_other, relay)
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

                if not delivered:
                    await notify_manager.send_to(uid_other, {"type": "new_message", **msg_dict})
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

            # If recipient isn't in this chat room, push via notify_manager so
            # any open screen (ChatsPage, FeedScreen, etc.) can handle it.
            if not delivered:
                await notify_manager.send_to(uid_other, {
                    "type":    "new_message",
                    **msg_dict,
                })

            # Push notification if the recipient is offline
            if not delivered:
                sender_name = current_user.full_name or "Someone"
                # Friendly preview — don't expose raw URLs in notifications
                if msg_type == "image":
                    preview = "📷 Sent a photo"
                elif msg_type == "voice":
                    preview = "🎙️ Sent a voice message"
                elif msg_type in ("tod_invite",):
                    preview = "🎯 Wants to play Truth or Dare"
                elif msg_type in ("tod_answer",):
                    preview = "🎯 Answered your Truth or Dare"
                elif msg_type in ("question_cards", "wyr", "hot_takes", "nhi"):
                    preview = "🎮 Sent a game card"
                else:
                    preview = content[:80]
                await send_push_notification(
                    other_push_token,
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

    result = await db.execute(query)
    messages = list(reversed(result.scalars().all()))

    return {"messages": [_msg_to_dict(m) for m in messages], "total": len(messages)}


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

    # If recipient isn't in this chat room, deliver via notify_manager
    if not delivered:
        await notify_manager.send_to(str(other_uuid), {
            "type": "new_message",
            **msg_dict,
        })

    if not delivered:
        sender_name = current_user.full_name or "Someone"
        await send_push_notification(
            other_user.push_token,
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

    await db.execute(
        update(Message)
        .where(
            Message.room_id == room_id,
            Message.sender_id == other_uuid,
            Message.receiver_id == current_user.id,
            Message.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    await db.commit()

    # Notify the sender that messages were read
    await manager.send_to(str(other_uuid), {"type": "read", "reader_id": str(current_user.id)})

    return {"ok": True}
