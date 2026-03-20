"""
Gift Card endpoints — secure redemption with device + IP tracking.

Rules:
  • Gift cards are only available for "weekly" (14 days) or "monthly" (30 days) plans.
  • Each user account may redeem a maximum of 2 gift cards total.
  • The 16-character code (XXXX-XXXX-XXXX-XXXX) is cryptographically random.
  • Every redemption records the device_id and IP address of the redeemer.
  • Every code has a mandatory expiry set at creation time.

Endpoints:
  POST /gift-cards              [admin] create codes
  POST /gift-cards/redeem       [any user] redeem a code
  GET  /gift-cards              [admin] list all codes
  GET  /gift-cards/{code}       [admin] inspect a single code
"""

import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.gift_card import GiftCard, _generate_code, _MAX_REDEMPTIONS_PER_USER
from app.models.subscription_plan import SubscriptionPlan
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gift-cards", tags=["gift-cards"])

# ─── Allowed plan intervals & durations ───────────────────────────────────────

ALLOWED_INTERVALS: dict[str, int] = {
    "weekly":  14,   # 2 weeks max
    "monthly": 30,   # 1 month max
}


# ─── Admin dependency ─────────────────────────────────────────────────────────

async def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user


# ─── IP helper ────────────────────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    """Best-effort real IP — respects X-Forwarded-For from reverse proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


# ─── Schemas ──────────────────────────────────────────────────────────────────

class GiftCardCreateRequest(BaseModel):
    plan_id: str = Field(..., description="UUID of a weekly or monthly SubscriptionPlan")
    quantity: int = Field(1, ge=1, le=100)
    expires_in_days: int = Field(30, ge=1, le=365, description="Days until the code itself expires")
    note: str | None = Field(None, max_length=256)


class GiftCardResponse(BaseModel):
    id: str
    code: str
    plan_name: str
    plan_interval: str
    duration_days: int
    is_redeemed: bool
    redeemed_at: datetime | None
    redeemed_device_id: str | None
    redeemed_ip_address: str | None
    expires_at: datetime | None
    purchased_by_user_id: str | None
    redeemed_by_user_id: str | None
    note: str | None
    created_at: datetime


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, description="16-char code, e.g. A3KP-7MNQ-Z9TW-2BXV")
    device_id: str = Field(..., min_length=1, max_length=256, description="Unique device identifier from the app")


class RedeemResponse(BaseModel):
    message: str
    tier: str
    expires_at: datetime | None
    plan_name: str
    duration_days: int
    redemptions_used: int
    redemptions_remaining: int


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _card_to_dict(card: GiftCard) -> dict[str, Any]:
    return {
        "id":                   str(card.id),
        "code":                 card.code,
        "plan_name":            card.plan.name if card.plan else "Unknown",
        "plan_interval":        card.plan_interval,
        "duration_days":        card.duration_days,
        "is_redeemed":          card.is_redeemed,
        "redeemed_at":          card.redeemed_at,
        "redeemed_device_id":   card.redeemed_device_id,
        "redeemed_ip_address":  card.redeemed_ip_address,
        "expires_at":           card.expires_at,
        "purchased_by_user_id": str(card.purchased_by_user_id) if card.purchased_by_user_id else None,
        "redeemed_by_user_id":  str(card.redeemed_by_user_id) if card.redeemed_by_user_id else None,
        "note":                 card.note,
        "created_at":           card.created_at,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=list[GiftCardResponse],
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create gift card codes for a weekly or monthly plan",
)
async def create_gift_cards(
    body: GiftCardCreateRequest,
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Any]:
    result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == _uuid.UUID(body.plan_id))
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Subscription plan not found.")

    if plan.interval not in ALLOWED_INTERVALS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Gift cards only support 'weekly' (14 days) and 'monthly' (30 days) plans. "
                f"Plan '{plan.name}' has interval '{plan.interval}'."
            ),
        )

    duration_days = ALLOWED_INTERVALS[plan.interval]
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    cards: list[GiftCard] = []
    for _ in range(body.quantity):
        card = GiftCard(
            code=_generate_code(),
            plan_id=plan.id,
            plan_interval=plan.interval,
            duration_days=duration_days,
            purchased_by_user_id=admin.id,
            expires_at=expires_at,
            note=body.note,
        )
        db.add(card)
        cards.append(card)

    await db.commit()
    for card in cards:
        await db.refresh(card)
        await db.refresh(card, ["plan"])

    logger.info(
        "Admin %s created %d gift card(s) — plan=%s interval=%s duration=%dd expires=%s",
        admin.id, len(cards), plan.name, plan.interval, duration_days,
        expires_at.strftime("%Y-%m-%d"),
    )
    return [_card_to_dict(c) for c in cards]


@router.post(
    "/redeem",
    response_model=RedeemResponse,
    summary="Redeem a gift card code — grants Pro access (max 2 per account)",
)
async def redeem_gift_card(
    body: RedeemRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    now = datetime.now(timezone.utc)
    ip_address = _get_client_ip(request)
    code = body.code.strip().upper()

    # ── 1. Per-account redemption limit ──────────────────────────────────────
    count_result = await db.execute(
        select(func.count()).select_from(GiftCard).where(
            GiftCard.redeemed_by_user_id == current_user.id,
            GiftCard.is_redeemed.is_(True),
        )
    )
    redemptions_used: int = count_result.scalar_one()

    if redemptions_used >= _MAX_REDEMPTIONS_PER_USER:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Account limit reached. Each account may redeem a maximum of "
                f"{_MAX_REDEMPTIONS_PER_USER} gift cards."
            ),
        )

    # ── 2. Look up the code ───────────────────────────────────────────────────
    result = await db.execute(select(GiftCard).where(GiftCard.code == code))
    card = result.scalar_one_or_none()

    if not card:
        logger.warning("Invalid gift card code attempt: %s | user=%s | ip=%s", code, current_user.id, ip_address)
        raise HTTPException(status_code=404, detail="Gift card code not found.")

    if card.is_redeemed:
        raise HTTPException(status_code=409, detail="This gift card has already been redeemed.")

    # ── 3. Expiry check ───────────────────────────────────────────────────────
    if card.expires_at:
        exp = card.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            raise HTTPException(status_code=410, detail="This gift card code has expired.")

    # ── 4. Load plan name ─────────────────────────────────────────────────────
    plan_name = "Zod Pro"
    if card.plan_id:
        plan_result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == card.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan:
            plan_name = plan.name

    # ── 5. Extend subscription (stack on top if already Pro) ──────────────────
    current_expires = current_user.subscription_expires_at
    if current_expires:
        base = current_expires if current_expires.tzinfo else current_expires.replace(tzinfo=timezone.utc)
        base = max(base, now)
    else:
        base = now

    new_expires = base + timedelta(days=card.duration_days)

    current_user.subscription_tier = "pro"
    current_user.subscription_expires_at = new_expires

    # ── 6. Mark redeemed + record security fingerprint ───────────────────────
    card.is_redeemed         = True
    card.redeemed_by_user_id = current_user.id
    card.redeemed_at         = now
    card.redeemed_device_id  = body.device_id
    card.redeemed_ip_address = ip_address

    await db.commit()

    redemptions_now = redemptions_used + 1
    remaining = max(0, _MAX_REDEMPTIONS_PER_USER - redemptions_now)

    logger.info(
        "Gift card redeemed: code=%s user=%s device=%s ip=%s pro_until=%s (%d/%d used)",
        code, current_user.id, body.device_id, ip_address,
        new_expires.strftime("%Y-%m-%d"),
        redemptions_now, _MAX_REDEMPTIONS_PER_USER,
    )

    return {
        "message": f"Gift card redeemed! You now have Zod Pro for {card.duration_days} days.",
        "tier": "pro",
        "expires_at": new_expires,
        "plan_name": plan_name,
        "duration_days": card.duration_days,
        "redemptions_used": redemptions_now,
        "redemptions_remaining": remaining,
    }


@router.get(
    "",
    response_model=list[GiftCardResponse],
    summary="[Admin] List gift cards",
)
async def list_gift_cards(
    redeemed: bool | None = Query(None),
    interval: str | None = Query(None, description="Filter by plan_interval: weekly | monthly"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Any]:
    q = select(GiftCard).order_by(GiftCard.created_at.desc()).limit(limit).offset(offset)
    if redeemed is not None:
        q = q.where(GiftCard.is_redeemed.is_(redeemed))
    if interval:
        q = q.where(GiftCard.plan_interval == interval)

    result = await db.execute(q)
    cards = result.scalars().all()
    for card in cards:
        if card.plan_id:
            await db.refresh(card, ["plan"])

    return [_card_to_dict(c) for c in cards]


class MyHistoryItem(BaseModel):
    code: str
    plan_name: str
    plan_interval: str
    duration_days: int
    redeemed_at: datetime
    expires_subscription_at: datetime | None


class MyHistoryResponse(BaseModel):
    redemptions_used: int
    redemptions_remaining: int
    max_redemptions: int
    history: list[MyHistoryItem]


@router.get(
    "/my-history",
    response_model=MyHistoryResponse,
    summary="Get the current user's gift card redemption history",
)
async def my_redemption_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Returns this account's redemption history and how many slots remain.
    """
    result = await db.execute(
        select(GiftCard)
        .where(
            GiftCard.redeemed_by_user_id == current_user.id,
            GiftCard.is_redeemed.is_(True),
        )
        .order_by(GiftCard.redeemed_at.desc())
    )
    cards = result.scalars().all()

    history: list[dict] = []
    for card in cards:
        plan_name = "Zod Pro"
        if card.plan_id:
            plan_res = await db.execute(
                select(SubscriptionPlan).where(SubscriptionPlan.id == card.plan_id)
            )
            plan = plan_res.scalar_one_or_none()
            if plan:
                plan_name = plan.name
        history.append({
            "code":                   card.code,
            "plan_name":              plan_name,
            "plan_interval":          card.plan_interval,
            "duration_days":          card.duration_days,
            "redeemed_at":            card.redeemed_at,
            "expires_subscription_at": None,
        })

    used = len(cards)
    return {
        "redemptions_used":      used,
        "redemptions_remaining": max(0, _MAX_REDEMPTIONS_PER_USER - used),
        "max_redemptions":       _MAX_REDEMPTIONS_PER_USER,
        "history":               history,
    }


@router.get(
    "/{code}",
    response_model=GiftCardResponse,
    summary="[Admin] Inspect a gift card by code",
)
async def get_gift_card(
    code: str,
    _: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Any:
    result = await db.execute(
        select(GiftCard).where(GiftCard.code == code.strip().upper())
    )
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Gift card not found.")
    if card.plan_id:
        await db.refresh(card, ["plan"])
    return _card_to_dict(card)
