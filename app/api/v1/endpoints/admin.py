"""
Admin endpoints (internal use only — authenticated users with admin flag or dev builds).

  GET  /admin/verifications                      — all verification attempts across all users
  GET  /admin/verifications/{id}                 — single attempt detail
  POST /admin/users/bypass-location-filter       — toggle worldwide location bypass for a user
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.verification import VerificationAttempt

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that rejects any non-admin user with a 403."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


def _attempt_dict(a: VerificationAttempt, user: User | None) -> dict:
    return {
        "id":               str(a.id),
        "status":           a.status,
        "submitted_at":     a.submitted_at.isoformat(),
        "processed_at":     a.processed_at.isoformat() if a.processed_at else None,
        "ip_address":       a.ip_address,
        "device_model":     a.device_model,
        "platform":         a.platform,
        "selfie_url":       a.selfie_url,
        "is_live":          a.is_live,
        "face_match_score": a.face_match_score,
        "age_estimate":     a.age_estimate,
        "rejection_reason": a.rejection_reason,
        "user": {
            "id":        str(user.id) if user else None,
            "full_name": user.full_name if user else None,
            "phone":     user.phone if user else None,
            "email":     user.email if user else None,
            "verification_status": user.verification_status if user else None,
            "is_verified": user.is_verified if user else None,
        } if user else None,
    }


@router.get("/verifications", summary="All verification attempts across all users")
async def list_all_verifications(
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VerificationAttempt)
        .order_by(VerificationAttempt.submitted_at.desc())
    )
    attempts = result.scalars().all()

    # Batch-load unique users
    user_ids = list({a.user_id for a in attempts})
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_map: dict = {u.id: u for u in users_result.scalars().all()}

    return {
        "total": len(attempts),
        "attempts": [_attempt_dict(a, users_map.get(a.user_id)) for a in attempts],
    }


@router.get("/verifications/{attempt_id}", summary="Single verification attempt detail")
async def get_verification_detail(
    attempt_id: UUID,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    attempt: VerificationAttempt | None = await db.get(VerificationAttempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found.")
    user: User | None = await db.get(User, attempt.user_id)
    return _attempt_dict(attempt, user)


# ── Location filter bypass ─────────────────────────────────────────────────────

class BypassLocationRequest(BaseModel):
    phone: str
    enabled: bool = True   # True = bypass ON (worldwide), False = restore normal


@router.post(
    "/users/bypass-location-filter",
    summary="Enable or disable worldwide location bypass for a user (by phone)",
)
async def set_bypass_location_filter(
    body: BypassLocationRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    When enabled=True the target user's discover feed ignores all distance
    filtering — they see profiles from any location worldwide regardless of
    what filter_max_distance_km is set to.
    """
    result = await db.execute(select(User).where(User.phone == body.phone))
    target: User | None = result.scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user found with phone {body.phone}",
        )

    target.bypass_location_filter = body.enabled
    db.add(target)
    await db.commit()

    _log.info(
        "Admin %s set bypass_location_filter=%s for user %s (phone %s)",
        current_user.id, body.enabled, target.id, body.phone,
    )
    return {
        "user_id": str(target.id),
        "phone": target.phone,
        "bypass_location_filter": target.bypass_location_filter,
    }
