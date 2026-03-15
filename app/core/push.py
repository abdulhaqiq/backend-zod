"""
Push notification service.

Supports two delivery mechanisms:
  • Expo Push API  — for tokens starting with "ExponentPushToken["
    Works with Expo Go and any Expo-built app. No extra credentials needed.
  • Firebase Admin (FCM) — for raw FCM registration tokens.
    Requires GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT_JSON
    env var to be set.

Usage:
    from app.core.push import send_push_notification
    await send_push_notification(token, title, body, data={...})
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# ── Firebase Admin lazy init ───────────────────────────────────────────────────

_firebase_app = None


def _get_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    try:
        import firebase_admin
        from firebase_admin import credentials

        sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if sa_json:
            cred = credentials.Certificate(json.loads(sa_json))
        else:
            cred = credentials.ApplicationDefault()

        _firebase_app = firebase_admin.initialize_app(cred)
        _log.info("Firebase Admin SDK initialized")
        return _firebase_app
    except Exception as exc:
        _log.warning("Firebase Admin SDK not available: %s", exc)
        return None


# ── Expo Push API ─────────────────────────────────────────────────────────────

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def _send_expo(token: str, title: str, body: str, data: dict[str, Any]) -> None:
    payload = {
        "to": token,
        "title": title,
        "body": body,
        "data": data,
        "sound": "default",
        "badge": 1,
        "channelId": "default",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _EXPO_PUSH_URL,
            json=payload,
            headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
        )
    if resp.status_code != 200:
        _log.warning("Expo push failed: status=%s body=%s", resp.status_code, resp.text[:200])
    else:
        result = resp.json()
        errors = [r for r in result.get("data", []) if r.get("status") == "error"]
        if errors:
            _log.warning("Expo push error: %s", errors)
        else:
            _log.info("Expo push sent: token=%s…", token[:24])


# ── FCM via Firebase Admin ─────────────────────────────────────────────────────

async def _send_fcm(token: str, title: str, body: str, data: dict[str, Any]) -> None:
    import asyncio

    def _blocking_send():
        try:
            from firebase_admin import messaging

            app = _get_firebase_app()
            if app is None:
                return

            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={k: str(v) for k, v in data.items()},
                token=token,
                android=messaging.AndroidConfig(priority="high"),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(sound="default", badge=1)
                    )
                ),
            )
            messaging.send(msg, app=app)
            _log.info("FCM push sent: token=%s…", token[:24])
        except Exception as exc:
            _log.warning("FCM send failed: %s", exc)

    await asyncio.to_thread(_blocking_send)


# ── Public API ────────────────────────────────────────────────────────────────

async def send_push_notification(
    token: str | None,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Send a push notification. Silently skips if token is absent."""
    if not token:
        return
    data = data or {}
    try:
        if token.startswith("ExponentPushToken"):
            await _send_expo(token, title, body, data)
        else:
            await _send_fcm(token, title, body, data)
    except Exception as exc:
        _log.warning("Push notification failed (skipped): %s", exc)
