"""
Push-notification gateway — Expo Push Notification Service.

The mobile app registers a device push token via expo-notifications and saves
it to the backend via POST /profile/me/push-token.  The backend forwards
notifications to Expo's push service which delivers them via APNs (iOS).

Token format:  ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]
API endpoint:  https://exp.host/--/api/v2/push/send
Pricing:       Free, unlimited for development; generous rate limits for production.

Usage
-----
    from app.core.push import send_push_notification

    await send_push_notification(
        user.push_token,
        title="New match!",
        body="Someone liked you",
        data={"type": "match"},
    )

If push_token is None or empty the call is a no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)

# ── Notification type configuration ───────────────────────────────────────────
#
# TRANSACTIONAL ("activity" channel) — high priority, sound+vibration
#   match, liked_you, super_like, chat/message, audio_message, profile_view, call
#
# MARKETING ("marketing" channel) — normal priority, no sound
#   ai_picks, promotions, dating_tips, weekly_digest

_NOTIF_TYPE_CONFIG: dict[str, tuple[str, str, str]] = {
    "match":           ("activity",      "active",         "high"),
    "liked_you":       ("activity",      "active",         "high"),
    "super_like":      ("activity",      "time-sensitive", "high"),
    "chat":            ("activity",      "active",         "high"),
    "message":         ("activity",      "active",         "high"),
    "audio_message":   ("activity",      "active",         "high"),
    "profile_view":    ("activity",      "active",         "normal"),
    "call":            ("incoming_call", "time-sensitive", "high"),
    "ai_picks":        ("marketing",     "passive",        "normal"),
    "promotions":      ("marketing",     "passive",        "normal"),
    "dating_tips":     ("marketing",     "passive",        "normal"),
    "weekly_digest":   ("marketing",     "passive",        "normal"),
}

_NOTIF_TYPE_TO_PREF: dict[str, str] = {
    "match":         "notif_new_match",
    "message":       "notif_new_message",
    "chat":          "notif_new_message",
    "audio_message": "notif_new_message",
    "super_like":    "notif_super_like",
    "liked_you":     "notif_liked_profile",
    "profile_view":  "notif_profile_views",
    "ai_picks":      "notif_ai_picks",
    "promotions":    "notif_promotions",
    "dating_tips":   "notif_dating_tips",
}


async def notify_user(
    user: Any,
    notif_type: str,
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    badge: int | None = None,
) -> None:
    """
    Send a push notification to a user, respecting their per-category preferences.

    Channel and priority are inferred automatically from notif_type.
    """
    if user is None:
        return
    pref_field = _NOTIF_TYPE_TO_PREF.get(notif_type)
    if pref_field and not getattr(user, pref_field, True):
        _log.debug("push | skipped (user pref off) type=%s user=%s", notif_type, getattr(user, "id", "?"))
        return

    channel_id, _interruption, priority = _NOTIF_TYPE_CONFIG.get(
        notif_type, ("activity", "active", "high")
    )

    await send_push_notification(
        getattr(user, "push_token", None),
        title=title,
        body=body,
        data={**(data or {}), "type": notif_type},
        channel_id=channel_id,
        priority=priority,
        badge=badge,
    )


async def send_push_notification(
    push_token: str | None,
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    channel_id: str = "activity",
    priority: str = "high",
    badge: int | None = None,
    notif_type: str | None = None,
) -> None:
    """
    Send a single push notification via Expo's push service.

    Parameters
    ----------
    push_token:  Expo push token (ExponentPushToken[...]) stored on the User model.
    title:       Bold headline shown in the notification banner.
    body:        Notification body text (message preview, etc.).
    data:        Extra payload delivered to the app when tapped.
    channel_id:  Android channel — "activity" | "marketing" | "incoming_call".
    priority:    "high" (transactional) | "normal" (marketing).
    badge:       iOS badge count override (omit to leave unchanged).
    notif_type:  Informational — used for logging.
    """
    if not push_token:
        return

    if not push_token.startswith("ExponentPushToken["):
        _log.warning("push | unrecognised token format (expected ExponentPushToken[...]) — skipping")
        return

    is_marketing = channel_id == "marketing"

    payload: dict[str, Any] = {
        "to":       push_token,
        "title":    title,
        "body":     body,
        "sound":    None if is_marketing else "default",
        "priority": "high" if priority == "high" else "normal",
        "channelId": channel_id,
    }
    if data:
        payload["data"] = data
    if badge is not None:
        payload["badge"] = badge

    encoded = json.dumps(payload).encode("utf-8")

    def _send() -> None:
        req = urllib.request.Request(
            "https://exp.host/--/api/v2/push/send",
            data=encoded,
            headers={
                "Content-Type":   "application/json",
                "Accept":         "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

        item = result.get("data", {})
        status = item.get("status")
        if status == "error":
            _log.warning(
                "push | Expo error token=%s… details=%s",
                push_token[:30], item.get("details", {}),
            )
        else:
            _log.debug("push | sent token=%s… type=%s", push_token[:30], notif_type)

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        _log.warning("push | Expo push error: %s", exc)
