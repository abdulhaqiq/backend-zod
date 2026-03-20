"""
Expo Push Notification helper.

Uses the Expo push API (https://exp.host/--/api/v2/push/send) which works
for both iOS (APNs) and Android (FCM) through a single endpoint.
No SDK credentials needed — only the ExponentPushToken saved per-user.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

_log = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_push_notification(
    token: str | None,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    sound: str = "default",
    badge: int | None = None,
) -> None:
    """Fire-and-forget: send one Expo push notification.

    Silently does nothing if the token is missing or not an Expo token.
    Network errors are logged but never raised — push is best-effort.
    """
    if not token or not token.startswith("ExponentPushToken"):
        return

    payload: dict[str, Any] = {
        "to":    token,
        "title": title,
        "body":  body,
        "sound": sound,
        "data":  data or {},
    }
    if badge is not None:
        payload["badge"] = badge

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=payload,
                headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
            )
            if resp.status_code >= 400:
                _log.warning("Expo push error %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        _log.warning("push notification failed (token=%.20s…): %s", token, exc)


async def send_push_bulk(notifications: list[dict[str, Any]]) -> None:
    """Send multiple notifications in a single Expo batch request (max 100)."""
    if not notifications:
        return
    valid = [n for n in notifications if n.get("to", "").startswith("ExponentPushToken")]
    if not valid:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=valid,
                headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
            )
            if resp.status_code >= 400:
                _log.warning("Expo push bulk error %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        _log.warning("push bulk failed: %s", exc)
