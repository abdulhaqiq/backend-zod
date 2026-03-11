"""
Subscription endpoints — Apple IAP via RevenueCat.

Flow:
  1. App purchases via Apple IAP (react-native-purchases / RevenueCat SDK)
  2. RevenueCat validates receipt with Apple, grants entitlement
  3. App POSTs to  /subscription/verify  with RevenueCat customer_id
  4. Backend calls RevenueCat REST API to confirm entitlement, updates DB
  5. RevenueCat POSTs to /subscription/webhook on renewals / expirations

Required env vars:
  REVENUECAT_SECRET_KEY   — RevenueCat secret (V1) API key
  REVENUECAT_WEBHOOK_AUTH — webhook authorization header value (optional)
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscription", tags=["subscription"])

RC_BASE = "https://api.revenuecat.com/v1"

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _rc_get_customer(customer_id: str) -> dict:
    """Fetch subscriber info from RevenueCat REST API."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{RC_BASE}/subscribers/{customer_id}",
            headers={
                "Authorization": f"Bearer {settings.REVENUECAT_SECRET_KEY}",
                "X-Platform": "ios",
            },
        )
    if r.status_code != 200:
        raise HTTPException(502, f"RevenueCat error {r.status_code}: {r.text}")
    return r.json()


def _extract_entitlement(rc_data: dict) -> tuple[bool, datetime | None]:
    """Return (is_pro, expires_at) from RevenueCat subscriber payload."""
    subscriber = rc_data.get("subscriber", {})
    entitlements = subscriber.get("entitlements", {})
    pro = entitlements.get("pro") or entitlements.get("Pro")
    if not pro:
        return False, None
    expires_str = pro.get("expires_date")
    if not expires_str:
        return True, None  # lifetime
    expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
    is_active = expires_at > datetime.now(timezone.utc)
    return is_active, expires_at


# ─── Schemas ──────────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    revenuecat_customer_id: str


class StatusResponse(BaseModel):
    tier: str            # "free" | "pro"
    is_pro: bool
    expires_at: datetime | None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/config", summary="Return public RevenueCat SDK key for the client")
async def get_config(_: User = Depends(get_current_user)):
    """
    Returns the RevenueCat public iOS key so the frontend never needs it hardcoded.
    Requires a valid auth token so the key is not exposed to anonymous requests.
    """
    if not settings.REVENUECAT_PUBLIC_KEY:
        raise HTTPException(501, "RevenueCat not configured — set REVENUECAT_PUBLIC_KEY")
    return {"sdk_key": settings.REVENUECAT_PUBLIC_KEY}


@router.get("/status", response_model=StatusResponse, summary="Get current subscription status")
async def get_status(current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    expires = current_user.subscription_expires_at
    # Expire pro if past date
    if current_user.subscription_tier == "pro" and expires and expires < now:
        tier = "free"
    else:
        tier = current_user.subscription_tier
    return {
        "tier": tier,
        "is_pro": tier == "pro",
        "expires_at": expires,
    }


@router.post("/verify", response_model=StatusResponse, summary="Verify purchase with RevenueCat and unlock pro")
async def verify_purchase(
    body: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the app after a successful Apple IAP purchase.
    Validates entitlement with RevenueCat and upgrades the user.
    """
    if not settings.REVENUECAT_SECRET_KEY:
        raise HTTPException(501, "RevenueCat not configured — set REVENUECAT_SECRET_KEY")

    rc_data = await _rc_get_customer(body.revenuecat_customer_id)
    is_pro, expires_at = _extract_entitlement(rc_data)

    current_user.revenuecat_customer_id = body.revenuecat_customer_id
    current_user.subscription_tier = "pro" if is_pro else "free"
    current_user.subscription_expires_at = expires_at
    await db.commit()

    return {
        "tier": current_user.subscription_tier,
        "is_pro": is_pro,
        "expires_at": expires_at,
    }


@router.post("/webhook", summary="RevenueCat webhook — subscription lifecycle events")
async def revenuecat_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
):
    """
    RevenueCat sends events here: INITIAL_PURCHASE, RENEWAL, CANCELLATION,
    EXPIRATION, BILLING_ISSUE, etc.
    Configure the URL in RevenueCat dashboard → Integrations → Webhooks.
    Set Authorization header = REVENUECAT_WEBHOOK_AUTH.
    """
    # Validate webhook auth header
    expected = settings.REVENUECAT_WEBHOOK_AUTH
    if expected and authorization != expected:
        raise HTTPException(401, "Invalid webhook authorization")

    payload = await request.json()
    event = payload.get("event", {})
    event_type = event.get("type", "")
    rc_customer_id = event.get("app_user_id") or event.get("original_app_user_id")

    logger.info("RevenueCat webhook: %s for customer %s", event_type, rc_customer_id)

    if not rc_customer_id:
        return {"ok": True}

    # Find user by RevenueCat customer ID
    from sqlalchemy import select
    result = await db.execute(
        select(User).where(User.revenuecat_customer_id == rc_customer_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("Webhook: no user found for RC customer %s", rc_customer_id)
        return {"ok": True}

    if event_type in ("INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"):
        expires_str = event.get("expiration_at_ms")
        expires_at = (
            datetime.fromtimestamp(int(expires_str) / 1000, tz=timezone.utc)
            if expires_str else None
        )
        user.subscription_tier = "pro"
        user.subscription_expires_at = expires_at
    elif event_type in ("CANCELLATION", "EXPIRATION", "BILLING_ISSUE"):
        user.subscription_tier = "free"

    await db.commit()
    logger.info("User %s subscription → %s", user.id, user.subscription_tier)
    return {"ok": True}
