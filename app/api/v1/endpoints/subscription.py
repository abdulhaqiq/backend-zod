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


async def _expire_if_needed(user: User, db: AsyncSession) -> str:
    """
    Check if a paid subscription has lapsed; if so, downgrade to 'free' in DB.
    Returns the current (possibly updated) tier string.
    Called by status, get_pro_user, and verify so expiry is always enforced.
    """
    tier = user.subscription_tier
    if tier not in ("pro", "premium_plus"):
        return tier
    expires = user.subscription_expires_at
    if expires is None:
        return tier  # lifetime / no-expiry grant
    now = datetime.now(timezone.utc)
    expires_aware = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
    if expires_aware < now:
        logger.info(
            "Auto-expiring subscription for user %s (tier=%s, expired=%s)",
            user.id, tier, expires_aware,
        )
        user.subscription_tier = "free"
        user.subscription_expires_at = None
        await db.commit()
        return "free"
    return tier


def _extract_entitlement(rc_data: dict) -> tuple[bool, str, datetime | None]:
    """Return (is_active, tier, expires_at) from RevenueCat subscriber payload.

    Checks both 'premium' and 'pro' entitlements; premium takes precedence.
    tier is one of: 'premium_plus' | 'pro' | 'free'
    """
    subscriber = rc_data.get("subscriber", {})
    entitlements = subscriber.get("entitlements", {})
    now = datetime.now(timezone.utc)

    for rc_key, tier in [("premium", "premium_plus"), ("Premium", "premium_plus"),
                          ("pro", "pro"), ("Pro", "pro")]:
        ent = entitlements.get(rc_key)
        if not ent:
            continue
        expires_str = ent.get("expires_date")
        if not expires_str:
            return True, tier, None  # lifetime
        expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
        if expires_at > now:
            return True, tier, expires_at

    return False, "free", None


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
    interval: str            # "weekly" | "monthly" | "threemonth" | "annual"
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
    interval: str = Field(..., pattern="^(weekly|monthly|threemonth|annual)$")
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
    interval: str | None = Field(None, pattern="^(weekly|monthly|threemonth|annual)$")
    price_display: str | None = Field(None, max_length=32)
    price_usd: float | None = Field(None, gt=0)
    badge: str | None = None
    description: str | None = None
    features: list[Any] | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class GrantRequest(BaseModel):
    user_id: str
    tier: str = Field("pro", pattern="^(free|pro|premium_plus)$")
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
    # Free-tier daily like quota
    daily_likes_limit: int
    daily_likes_used: int
    daily_likes_remaining: int
    # AI Credits wallet
    ai_credits_balance: int
    ai_credits_monthly: int   # monthly grant for this tier (0 for free)


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

    FREE_DAILY_LIKE_LIMIT = 20
    AI_CREDITS_MONTHLY = {"free": 0, "pro": 10, "premium_plus": 25}

    # ── Free users: no plan lookup needed ────────────────────────────────────
    if tier == "free":
        # Auto-reset daily likes counter if it's a new UTC day
        daily_reset = current_user.daily_likes_reset_at
        if daily_reset is None or daily_reset.date() < now.date():
            current_user.daily_likes_used = 0
            current_user.daily_likes_reset_at = now
            await db.commit()
        daily_used      = current_user.daily_likes_used
        daily_remaining = max(0, FREE_DAILY_LIKE_LIMIT - daily_used)
        return {
            "tier":                       "free",
            "super_likes_limit":          0,
            "super_likes_remaining":      0,
            "super_likes_reset_at":       None,
            "super_likes_resets_in_days": None,
            "profile_boosts_limit":       0,
            "features":                   [],
            "daily_likes_limit":          FREE_DAILY_LIKE_LIMIT,
            "daily_likes_used":           daily_used,
            "daily_likes_remaining":      daily_remaining,
            "ai_credits_balance":         current_user.ai_credits_balance,
            "ai_credits_monthly":         0,
        }

    # ── Paid tiers: hardcoded safety defaults ─────────────────────────────────
    DEFAULTS = {
        "pro":          {"sl_limit": 5,  "boost_limit": 1},
        "premium_plus": {"sl_limit": 10, "boost_limit": 2},
    }
    defaults = DEFAULTS[tier]  # safe — we've already handled "free" above

    # Complete canonical feature list used when the DB plan has no structured features.
    FALLBACK_FEATURES: dict[str, list[dict]] = {
        "pro": [
            {"key": "unlimited_likes",    "label": "Unlimited likes",    "icon": "heart",            "type": "bool",     "value": True},
            {"key": "see_who_liked_you",  "label": "See who liked you",  "icon": "eye",              "type": "bool",     "value": True},
            {"key": "rewind",             "label": "Rewind last swipe",  "icon": "refresh-circle",   "type": "bool",     "value": True},
            {"key": "super_likes",        "label": "Super Likes",        "icon": "star",             "type": "quantity", "limit": 5,  "period": "weekly",  "display": "5/wk"},
            {"key": "profile_boosts",     "label": "Profile Boosts",     "icon": "rocket",           "type": "quantity", "limit": 1,  "period": "monthly", "display": "1/mo"},
            {"key": "advanced_filters",   "label": "Advanced filters",   "icon": "options",          "type": "bool",     "value": True},
            {"key": "ai_smart_matching",  "label": "AI Smart Matching",  "icon": "sparkles",         "type": "bool",     "value": True},
            {"key": "ai_credits",         "label": "AI Credits",         "icon": "flash",            "type": "quantity", "limit": 10, "period": "monthly", "display": "10/mo"},
            {"key": "travel_mode",        "label": "Travel Mode",        "icon": "airplane",         "type": "bool",     "value": True},
            {"key": "priority_visibility","label": "Priority visibility","icon": "trending-up",      "type": "bool",     "value": True},
            {"key": "read_receipts",      "label": "Read receipts",      "icon": "chatbubble",       "type": "bool",     "value": False},
            {"key": "no_ads",             "label": "No ads",             "icon": "ban",              "type": "bool",     "value": True},
            {"key": "incognito",          "label": "Incognito browsing", "icon": "eye-off",          "type": "bool",     "value": False},
            {"key": "vip_support",        "label": "VIP support",        "icon": "shield-checkmark", "type": "bool",     "value": False},
        ],
        "premium_plus": [
            {"key": "unlimited_likes",    "label": "Unlimited likes",    "icon": "heart",            "type": "bool",     "value": True},
            {"key": "see_who_liked_you",  "label": "See who liked you",  "icon": "eye",              "type": "bool",     "value": True},
            {"key": "rewind",             "label": "Rewind last swipe",  "icon": "refresh-circle",   "type": "bool",     "value": True},
            {"key": "super_likes",        "label": "Super Likes",        "icon": "star",             "type": "quantity", "limit": 10, "period": "weekly",  "display": "10/wk"},
            {"key": "profile_boosts",     "label": "Profile Boosts",     "icon": "rocket",           "type": "quantity", "limit": 2,  "period": "monthly", "display": "2/mo"},
            {"key": "advanced_filters",   "label": "Advanced filters",   "icon": "options",          "type": "bool",     "value": True},
            {"key": "ai_smart_matching",  "label": "AI Smart Matching",  "icon": "sparkles",         "type": "label",    "display": "Priority"},
            {"key": "ai_credits",         "label": "AI Credits",         "icon": "flash",            "type": "quantity", "limit": 25, "period": "monthly", "display": "25/mo"},
            {"key": "travel_mode",        "label": "Travel Mode",        "icon": "airplane",         "type": "bool",     "value": True},
            {"key": "priority_visibility","label": "Priority visibility","icon": "trending-up",      "type": "label",    "display": "2×"},
            {"key": "read_receipts",      "label": "Read receipts",      "icon": "chatbubble",       "type": "bool",     "value": True},
            {"key": "no_ads",             "label": "No ads",             "icon": "ban",              "type": "bool",     "value": True},
            {"key": "incognito",          "label": "Incognito browsing", "icon": "eye-off",          "type": "bool",     "value": True},
            {"key": "vip_support",        "label": "VIP support",        "icon": "shield-checkmark", "type": "bool",     "value": True},
        ],
    }

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

    # If DB has no structured features, use the canonical hardcoded list
    if not features:
        features = FALLBACK_FEATURES.get(tier, [])

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

    # ── Monthly AI credits auto-grant ─────────────────────────────────────────
    monthly_grant = AI_CREDITS_MONTHLY.get(tier, 0)
    ai_reset = current_user.ai_credits_reset_at
    ai_reset_aware = ai_reset.replace(tzinfo=timezone.utc) if ai_reset and ai_reset.tzinfo is None else ai_reset
    new_month = (
        ai_reset_aware is None
        or ai_reset_aware.year < now.year
        or ai_reset_aware.month < now.month
    )
    if new_month and monthly_grant > 0:
        current_user.ai_credits_balance += monthly_grant
        current_user.ai_credits_reset_at = now
        await db.commit()

    return {
        "tier":                       tier,
        "super_likes_limit":          sl_limit,
        "super_likes_remaining":      current_user.super_likes_remaining,
        "super_likes_reset_at":       current_user.super_likes_reset_at,
        "super_likes_resets_in_days": resets_in_days,
        "profile_boosts_limit":       boost_limit,
        "features":                   features,
        "daily_likes_limit":          -1,   # -1 = unlimited for paid tiers
        "daily_likes_used":           0,
        "daily_likes_remaining":      -1,   # -1 = unlimited
        "ai_credits_balance":         current_user.ai_credits_balance,
        "ai_credits_monthly":         monthly_grant,
    }


@router.get("/status", response_model=StatusResponse, summary="Get current subscription status")
async def get_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Persist expiry to DB so all other endpoints see the correct tier
    tier = await _expire_if_needed(current_user, db)
    return {
        "tier": tier,
        "is_pro": tier in ("pro", "premium_plus"),
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
    is_active, new_tier, expires_at = _extract_entitlement(rc_data)

    current_user.revenuecat_customer_id = body.revenuecat_customer_id
    old_tier = current_user.subscription_tier
    current_user.subscription_tier = new_tier
    current_user.subscription_expires_at = expires_at

    # Seed super likes when a user first becomes paid
    upgrading = new_tier in ("pro", "premium_plus") and old_tier not in ("pro", "premium_plus")
    if upgrading and current_user.super_likes_remaining == 0 and current_user.super_likes_reset_at is None:
        current_user.super_likes_remaining = 10 if new_tier == "premium_plus" else 5
        current_user.super_likes_reset_at  = datetime.now(timezone.utc)

    await db.commit()

    return {
        "tier": current_user.subscription_tier,
        "is_pro": new_tier in ("pro", "premium_plus"),
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
        # Determine tier from the entitlement_ids in the event
        entitlement_ids: list = event.get("entitlement_ids") or []
        if any("premium" in e.lower() for e in entitlement_ids):
            new_tier = "premium_plus"
        else:
            new_tier = "pro"
        old_tier = user.subscription_tier
        user.subscription_tier = new_tier
        user.subscription_expires_at = expires_at
        # Seed super likes on first upgrade
        upgrading = new_tier in ("pro", "premium_plus") and old_tier not in ("pro", "premium_plus")
        if upgrading and user.super_likes_remaining == 0 and user.super_likes_reset_at is None:
            user.super_likes_remaining = 10 if new_tier == "premium_plus" else 5
            user.super_likes_reset_at  = datetime.now(timezone.utc)

    elif event_type == "EXPIRATION":
        # Subscription period actually ended — downgrade now.
        user.subscription_tier = "free"
        user.subscription_expires_at = None

    elif event_type == "CANCELLATION":
        # User turned off auto-renew but subscription is still active until the period ends.
        # Do NOT downgrade here — the EXPIRATION webhook will fire when it actually lapses.
        # Update the expiry date if provided so the auto-expire logic has the right value.
        expires_str = event.get("expiration_at_ms")
        if expires_str:
            user.subscription_expires_at = datetime.fromtimestamp(
                int(expires_str) / 1000, tz=timezone.utc
            )
        logger.info(
            "User %s cancelled auto-renew — remains active until %s",
            user.id, user.subscription_expires_at,
        )

    elif event_type == "BILLING_ISSUE":
        # Payment failed — Apple gives a grace period before expiring.
        # Keep the current tier active; update expiry to the grace-period end date if provided.
        # EXPIRATION will fire once the grace period lapses.
        expires_str = event.get("grace_period_expiration_at_ms") or event.get("expiration_at_ms")
        if expires_str:
            user.subscription_expires_at = datetime.fromtimestamp(
                int(expires_str) / 1000, tz=timezone.utc
            )
        logger.info(
            "User %s billing issue — grace period until %s",
            user.id, user.subscription_expires_at,
        )

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


# ─── AI Credits consumable purchase ──────────────────────────────────────────

# Pack definitions — must match App Store Connect & RevenueCat product IDs
_AI_CREDIT_PACKS: dict[str, int] = {
    "com.zod.ai.credits.101": 10,
    "com.zod.ai.credits.25":  25,
    "com.zod.ai.credits.50":  50,
}


class AiCreditsTopupRequest(BaseModel):
    pack_id: str                   # e.g. "com.zod.ai.credits.101"
    revenuecat_customer_id: str    # RC customer ID to verify the transaction


class AiCreditsTopupResponse(BaseModel):
    credits_added: int
    new_balance: int


@router.post(
    "/ai-credits/topup",
    response_model=AiCreditsTopupResponse,
    summary="Top up AI credits via consumable IAP (validated through RevenueCat)",
)
async def topup_ai_credits(
    body: AiCreditsTopupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Called after a successful consumable IAP purchase.
    Verifies the transaction via RevenueCat, then credits the user's AI wallet.
    """
    credits_to_add = _AI_CREDIT_PACKS.get(body.pack_id)
    if credits_to_add is None:
        raise HTTPException(400, f"Unknown pack_id: {body.pack_id!r}. Valid packs: {list(_AI_CREDIT_PACKS)}")

    # Verify with RevenueCat that this customer has a non-subscription purchase
    # for this product. For consumables, RC records them under non_subscriptions.
    if settings.REVENUECAT_SECRET_KEY:
        try:
            rc_data = await _rc_get_customer(body.revenuecat_customer_id)
            subscriber = rc_data.get("subscriber", {})
            non_subs: dict = subscriber.get("non_subscriptions", {})
            if body.pack_id not in non_subs:
                raise HTTPException(402, "RevenueCat: purchase not found for this product.")
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("RC topup verification warning: %s", exc)

    current_user.revenuecat_customer_id = body.revenuecat_customer_id
    current_user.ai_credits_balance += credits_to_add
    await db.commit()

    logger.info(
        "AI credits topup: user %s +%d credits (pack=%s) → balance=%d",
        current_user.id, credits_to_add, body.pack_id, current_user.ai_credits_balance,
    )
    return {"credits_added": credits_to_add, "new_balance": current_user.ai_credits_balance}


# ─── AI Credits spend (used by any AI feature) ───────────────────────────────

class AiCreditsSpendRequest(BaseModel):
    amount: int = Field(..., ge=1, le=100)
    reason: str = Field(..., max_length=128)   # e.g. "ai_match_score", "ai_bio_rewrite"


class AiCreditsSpendResponse(BaseModel):
    spent: int
    new_balance: int


@router.post(
    "/ai-credits/spend",
    response_model=AiCreditsSpendResponse,
    summary="Deduct AI credits for a feature action",
)
async def spend_ai_credits(
    body: AiCreditsSpendRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Deduct `amount` credits from the user's AI wallet. Returns 402 if insufficient."""
    if current_user.ai_credits_balance < body.amount:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient AI credits (have {current_user.ai_credits_balance}, need {body.amount}).",
        )
    current_user.ai_credits_balance -= body.amount
    await db.commit()
    logger.info(
        "AI credits spent: user %s -%d (%s) → balance=%d",
        current_user.id, body.amount, body.reason, current_user.ai_credits_balance,
    )
    return {"spent": body.amount, "new_balance": current_user.ai_credits_balance}


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
        user.super_likes_remaining = 0
        user.super_likes_reset_at  = None
    elif old_tier != body.tier and user.super_likes_remaining == 0 and user.super_likes_reset_at is None:
        # Seed super likes when manually granting Pro / Premium+ for the first time
        user.super_likes_remaining = 10 if body.tier == "premium_plus" else 5
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
