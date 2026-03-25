"""
Push-notification gateway — Expo Push API.

The mobile app registers an ExponentPushToken[…] (via expo-notifications) and
saves it to the backend via POST /profile/me/push-token.  Expo's gateway then
proxies the notification to FCM (Android) or APNs (iOS) transparently, so this
single implementation covers both platforms.

Usage
-----
    from app.core.push import send_push_notification

    await send_push_notification(
        user.push_token,
        title="💬 Alice",
        body="Hey, are you free tonight?",
        data={"type": "chat", "other_user_id": "..."},
    )

If push_token is None or empty the call is a no-op.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Optional: set EXPO_ACCESS_TOKEN in .env to authenticate with Expo's push service.
# Without it requests still work but are subject to stricter rate limits.
_expo_access_token: str | None = None
try:
    from app.core.config import settings
    _expo_access_token = getattr(settings, "EXPO_ACCESS_TOKEN", None) or None
except Exception:
    pass


# Maps notification event types to their User model preference field.
# If a type isn't listed here it's always sent (e.g. calls are critical).
_NOTIF_TYPE_TO_PREF: dict[str, str] = {
    "match":         "notif_new_match",
    "message":       "notif_new_message",
    "chat":          "notif_new_message",
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
    channel_id: str = "default",
    priority: str = "high",
    badge: int | None = None,
) -> None:
    """
    Send a push notification to a user, respecting their per-category
    notification preferences.

    Parameters
    ----------
    user:        ORM User object (must have push_token + notif_* fields).
    notif_type:  Event type string — see _NOTIF_TYPE_TO_PREF for mapping.
    """
    if user is None:
        return
    pref_field = _NOTIF_TYPE_TO_PREF.get(notif_type)
    if pref_field and not getattr(user, pref_field, True):
        _log.debug("push | skipped (user pref off) type=%s user=%s", notif_type, getattr(user, "id", "?"))
        return
    await send_push_notification(
        getattr(user, "push_token", None),
        title=title,
        body=body,
        data=data,
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
    channel_id: str = "default",
    priority: str = "high",
    badge: int | None = None,
) -> None:
    """
    Send a single push notification to a device via Expo's push gateway.

    Parameters
    ----------
    push_token:  ExponentPushToken[…] stored on the User model.
    title:       Bold headline (shown in the notification banner).
    body:        Notification body text.
    data:        Extra payload delivered to the app when the notification is
                 tapped (deep-linking, call metadata, etc.).
    channel_id:  Android notification channel ("default" or "incoming_call").
    priority:    "default" | "normal" | "high".
    badge:       iOS badge count override (omit to leave unchanged).
    """
    if not push_token:
        return

    message: dict[str, Any] = {
        "to":        push_token,
        "title":     title,
        "body":      body,
        "sound":     "default",
        "priority":  priority,
        "channelId": channel_id,
    }
    if data:
        message["data"] = data
    if badge is not None:
        message["badge"] = badge

    headers: dict[str, str] = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    if _expo_access_token:
        headers["Authorization"] = f"Bearer {_expo_access_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_EXPO_PUSH_URL, json=message, headers=headers)
            result = resp.json()

        ticket = result.get("data") if isinstance(result, dict) else None
        if isinstance(ticket, dict) and ticket.get("status") == "error":
            _log.warning(
                "push | delivery error token=%s… msg=%s details=%s",
                push_token[:24],
                ticket.get("message"),
                ticket.get("details"),
            )
        else:
            _log.debug("push | sent token=%s… status=%s", push_token[:24], ticket)

    except Exception as exc:
        _log.warning("push | HTTP error: %s", exc)
