"""
Subscription endpoints — Apple IAP via RevenueCat.

Flow:
  1. App fetches  GET /subscription/plans  to display available plans
  2. App purchases via Apple IAP (react-native-purchases / RevenueCat SDK)
     using the plan's apple_product_id
  3. RevenueCat validates receipt with Apple, grants entitlement
  4. App POSTs to  /subscription/verify  with RevenueCat customer_id
  5. Backend calls RevenueCat REST API to confirm entitlement, updates DB
  6. RevenueCat POSTs to /subscription/webhook on renewals / expirations

Note: Apple controls all billing, renewals, and refunds — this API cannot
      charge or cancel through Apple directly. What we CAN do:
        • Serve plan metadata from DB (price, features, apple_product_id)
        • Manually grant/revoke Pro access for any user (admin only)
        • Update plan metadata without shipping an app update

Required env vars:
  REVENUECAT_SECRET_KEY   — RevenueCat secret (V1) API key
  REVENUECAT_WEBHOOK_AUTH — webhook authorization header value (optional)
"""

import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.subscription_plan import SubscriptionPlan
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


class PlanResponse(BaseModel):
    id: str
    name: str
    tier: str                # "pro" | "premium_plus"
    apple_product_id: str
    interval: str            # "weekly" | "monthly" | "sixmonth" | "annual"
    price_display: str       # e.g. "$9.99/mo"
    price_usd: float
    badge: str | None        # e.g. "Best Value"
    description: str | None
    features: list[Any]      # structured dicts or legacy strings
    sort_order: int

    model_config = {"from_attributes": True}


class PlanCreateRequest(BaseModel):
    name: str = Field(..., max_length=64)
    apple_product_id: str = Field(..., max_length=128)
    interval: str = Field(..., pattern="^(weekly|monthly|sixmonth|annual)$")
    price_display: str = Field(..., max_length=32)
    price_usd: float = Field(..., gt=0)
    badge: str | None = Field(None, max_length=32)
    description: str | None = None
    features: list[Any] = []
    sort_order: int = 0
    is_active: bool = True


class PlanUpdateRequest(BaseModel):
    name: str | None = Field(None, max_length=64)
    apple_product_id: str | None = Field(None, max_length=128)
    interval: str | None = Field(None, pattern="^(weekly|monthly|sixmonth|annual)$")
    price_display: str | None = Field(None, max_length=32)
    price_usd: float | None = Field(None, gt=0)
    badge: str | None = None
    description: str | None = None
    features: list[Any] | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class GrantRequest(BaseModel):
    user_id: str
    tier: str = Field("pro", pattern="^(free|pro)$")
    note: str | None = None  # internal reason (e.g. "comp account", "support ticket")


# ─── Admin dependency ──────────────────────────────────────────────────────────

async def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user


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


class MyFeaturesResponse(BaseModel):
    tier: str
    super_likes_limit: int
    super_likes_remaining: int
    super_likes_reset_at: datetime | None
    super_likes_resets_in_days: int | None
    profile_boosts_limit: int
    features: list[dict]   # full structured feature list from the canonical plan


@router.get("/my-features", response_model=MyFeaturesResponse, summary="Get the current user's plan limits and remaining quotas")
async def get_my_features(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Returns the active plan's structured features for the current user,
    combined with live quota state (super_likes_remaining, reset date).
    """
    tier = current_user.subscription_tier  # "free" | "pro" | "premium_plus"
    now  = datetime.now(timezone.utc)

    # ── Free users: no plan lookup needed ────────────────────────────────────
    if tier == "free":
        return {
            "tier":                      "free",
            "super_likes_limit":         0,
            "super_likes_remaining":     0,
            "super_likes_reset_at":      None,
            "super_likes_resets_in_days": None,
            "profile_boosts_limit":      0,
            "features":                  [],
        }

    # ── Paid tiers: hardcoded safety defaults ─────────────────────────────────
    DEFAULTS = {
        "pro":          {"sl_limit": 5,  "boost_limit": 1},
        "premium_plus": {"sl_limit": 10, "boost_limit": 2},
    }
    defaults = DEFAULTS[tier]  # safe — we've already handled "free" above

    # ── Look up the best structured plan for this tier ────────────────────────
    tier_keyword = "Premium+" if tier == "premium_plus" else "Pro"
    result = await db.execute(
        select(SubscriptionPlan).where(
            SubscriptionPlan.is_active.is_(True),
            SubscriptionPlan.name.icontains(tier_keyword),
        ).order_by(SubscriptionPlan.sort_order.desc())
    )

    # Walk sorted plans; pick the first one that has proper structured dict features
    features: list[dict] = []
    for p in result.scalars().all():
        raw: list[Any] = list(p.features) if p.features else []
        dicts = [f for f in raw if isinstance(f, dict) and "key" in f]
        if dicts:
            features = dicts
            break

    # Extract limits — fall back to hardcoded defaults only if the feature is absent
    sl_limit    = next((int(f["limit"]) for f in features if f.get("key") == "super_likes"),    defaults["sl_limit"])
    boost_limit = next((int(f["limit"]) for f in features if f.get("key") == "profile_boosts"), defaults["boost_limit"])

    # ── Auto-initialise super_likes_remaining on first Pro access ─────────────
    # Triggers once when reset_at is NULL (never been set), seeding the weekly quota.
    if current_user.super_likes_reset_at is None:
        current_user.super_likes_remaining = sl_limit
        current_user.super_likes_reset_at  = now
        await db.commit()

    # ── Weekly reset: refill if 7 days have elapsed since last reset ──────────
    reset_at = current_user.super_likes_reset_at
    if reset_at is not None:
        reset_at_aware = reset_at if reset_at.tzinfo else reset_at.replace(tzinfo=timezone.utc)
        if (now - reset_at_aware).total_seconds() >= 7 * 24 * 3600:
            current_user.super_likes_remaining = sl_limit
            current_user.super_likes_reset_at  = now
            await db.commit()

    # ── Days until next weekly reset ─────────────────────────────────────────
    resets_in_days: int | None = None
    if current_user.super_likes_reset_at is not None:
        reset_aware = current_user.super_likes_reset_at
        if reset_aware.tzinfo is None:
            reset_aware = reset_aware.replace(tzinfo=timezone.utc)
        next_reset     = reset_aware + timedelta(days=7)
        delta          = (next_reset - now).total_seconds()
        resets_in_days = max(0, int(delta / 86400))

    return {
        "tier":                      tier,
        "super_likes_limit":         sl_limit,
        "super_likes_remaining":     current_user.super_likes_remaining,
        "super_likes_reset_at":      current_user.super_likes_reset_at,
        "super_likes_resets_in_days": resets_in_days,
        "profile_boosts_limit":      boost_limit,
        "features":                  features,
    }


@router.get("/status", response_model=StatusResponse, summary="Get current subscription status")
async def get_status(current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    expires = current_user.subscription_expires_at
    # Normalise to timezone-aware so comparison never throws
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    # Expire pro if past date
    if current_user.subscription_tier == "pro" and expires and expires < now:
        tier = "free"
    else:
        tier = current_user.subscription_tier
    return {
        "tier": tier,
        "is_pro": tier == "pro",
        "expires_at": current_user.subscription_expires_at,
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
    new_tier = "pro" if is_pro else "free"
    upgrading = (new_tier == "pro" and current_user.subscription_tier != "pro")
    current_user.subscription_tier = new_tier
    current_user.subscription_expires_at = expires_at

    # Seed super likes when a user first becomes Pro
    if upgrading and current_user.super_likes_remaining == 0 and current_user.super_likes_reset_at is None:
        current_user.super_likes_remaining = 5  # Pro default
        current_user.super_likes_reset_at  = datetime.now(timezone.utc)

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


# ─── Plan endpoints (public read, admin write) ────────────────────────────────

@router.get("/plans", response_model=list[PlanResponse], summary="List active subscription plans")
async def list_plans(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Any]:
    """
    Returns all active plans ordered by sort_order.
    The frontend uses apple_product_id to initiate the purchase via RevenueCat.
    """
    result = await db.execute(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order)
    )
    plans = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "tier": "premium_plus" if "Premium+" in p.name else "pro",
            "apple_product_id": p.apple_product_id,
            "interval": p.interval,
            "price_display": p.price_display,
            "price_usd": float(p.price_usd),
            "badge": p.badge,
            "description": p.description,
            "features": list(p.features or []),
            "sort_order": p.sort_order,
        }
        for p in plans
    ]


@router.post("/plans", summary="[Admin] Create a new subscription plan")
async def create_plan(
    body: PlanCreateRequest,
    _: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    plan = SubscriptionPlan(
        name=body.name,
        apple_product_id=body.apple_product_id,
        interval=body.interval,
        price_display=body.price_display,
        price_usd=body.price_usd,
        badge=body.badge,
        description=body.description,
        features=body.features,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    logger.info("Admin created subscription plan: %s (%s)", plan.name, plan.apple_product_id)
    return {"id": str(plan.id), "name": plan.name, "apple_product_id": plan.apple_product_id}


@router.patch("/plans/{plan_id}", summary="[Admin] Update a subscription plan")
async def update_plan(
    plan_id: str,
    body: PlanUpdateRequest,
    _: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == _uuid.UUID(plan_id))
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(plan, field, value)

    await db.commit()
    await db.refresh(plan)
    logger.info("Admin updated plan %s: %s", plan_id, list(update_data.keys()))
    return {"id": str(plan.id), "name": plan.name, "is_active": plan.is_active}


# ─── Admin: manually grant / revoke Pro ───────────────────────────────────────

@router.post("/grant", summary="[Admin] Manually grant or revoke Pro for a user")
async def admin_grant(
    body: GrantRequest,
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Override a user's subscription tier directly in the database.
    Useful for:
      • Comp / influencer accounts
      • Resolving support tickets
      • Testing without going through Apple IAP

    Note: RevenueCat webhooks can later override this if the user's Apple
    subscription changes. To keep a permanent comp, set subscription_expires_at
    to null (which this endpoint does when granting pro without expiry).
    """
    result = await db.execute(
        select(User).where(User.id == _uuid.UUID(body.user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    old_tier = user.subscription_tier
    user.subscription_tier = body.tier
    if body.tier == "free":
        user.subscription_expires_at = None
    elif old_tier != body.tier and user.super_likes_remaining == 0 and user.super_likes_reset_at is None:
        # Seed super likes when manually granting Pro for the first time
        user.super_likes_remaining = 5
        user.super_likes_reset_at  = datetime.now(timezone.utc)

    await db.commit()
    logger.info(
        "Admin %s manually set user %s tier: %s → %s. Note: %s",
        admin.id, user.id, old_tier, body.tier, body.note or "—",
    )
    return {
        "user_id": str(user.id),
        "name": user.full_name,
        "tier": user.subscription_tier,
        "previous_tier": old_tier,
    }
