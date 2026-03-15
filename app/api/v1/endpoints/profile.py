"""
Profile endpoints:
  GET  /profile/me             — return current user's full profile
  PATCH /profile/me            — update any subset of profile fields (onboarding + edit)
  POST  /profile/me/best-photo — analyze uploaded photos by quality, reorder best first
  POST  /profile/check-email   — check whether an email is already taken (public)
"""
import asyncio
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_current_user_allow_inactive
from app.core.photo_analyzer import _check_quality
from app.db.session import get_db
from app.models.user import User
from app.schemas.profile import MeResponse, ProfileUpdateRequest
from app.services.scoring import compute_and_save_score

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

# ── Allowlist: fields a user may set on their own profile ─────────────────────
# Sensitive fields (subscription_tier, is_active, is_verified, verification_status,
# face_match_score) are intentionally omitted — they are managed by other endpoints
# (subscription/webhook, admin, verification flow).
_ALLOWED_PROFILE_FIELDS: frozenset[str] = frozenset({
    "full_name", "email", "date_of_birth", "gender_id", "bio",
    "purpose", "interests", "values_list", "languages", "causes",
    "lifestyle",
    "height_cm",
    "prompts", "photos",
    "education_level_id", "looking_for_id", "family_plans_id",
    "have_kids_id", "star_sign_id", "religion_id", "ethnicity_id",
    "is_onboarded", "dark_mode", "best_photo_enabled",
    "city", "hometown", "address", "country", "latitude", "longitude",
    "voice_prompts", "work_experience", "education",
    "work_photos", "work_prompts", "work_matching_goals",
    "work_are_you_hiring", "work_commitment_level_id", "work_skills",
    "work_equity_split_id", "work_industries", "work_scheduling_url",
    "work_who_to_show_id", "work_priority_startup",
    # Discover filter preferences
    "filter_age_min", "filter_age_max", "filter_max_distance_km",
    "filter_verified_only", "filter_star_signs", "filter_interests",
    "filter_languages", "filter_purpose", "filter_looking_for",
    "filter_education_level", "filter_family_plans", "filter_have_kids",
    "filter_ethnicities", "filter_exercise", "filter_drinking", "filter_smoking",
    "filter_height_min", "filter_height_max",
    # Mood status
    "mood_emoji", "mood_text",
})

_LIFESTYLE_KEYS: frozenset[str] = frozenset({"drinking", "smoking", "exercise", "diet"})


@router.get("/me", response_model=MeResponse, summary="Get current user profile")
async def get_me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse.model_validate(current_user)


@router.patch("/me", response_model=MeResponse, summary="Update profile (onboarding or edit)")
async def update_me(
    payload: ProfileUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    update_data = payload.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update.",
        )

    # Validate lifestyle keys/values if provided
    if "lifestyle" in update_data and update_data["lifestyle"] is not None:
        lifestyle: dict = update_data["lifestyle"]
        invalid_keys = set(lifestyle.keys()) - _LIFESTYLE_KEYS
        if invalid_keys:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid lifestyle keys: {sorted(invalid_keys)}. Allowed: {sorted(_LIFESTYLE_KEYS)}.",
            )
        for k, v in lifestyle.items():
            if not isinstance(v, int) or v <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Lifestyle value for '{k}' must be a positive integer (lookup_options ID).",
                )

    for field, value in update_data.items():
        if field not in _ALLOWED_PROFILE_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field '{field}' cannot be updated via this endpoint.",
            )
        if field == "gender_id" and value not in (223, 224):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="gender_id must be 223 (Male) or 224 (Female).",
            )
        # Email uniqueness check — skip if same as current
        if field == "email" and value and value != current_user.email:
            existing = await db.execute(
                select(User.id).where(User.email == value, User.id != current_user.id)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This email address is already in use.",
                )
        setattr(current_user, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        _log.warning("Profile update integrity error for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="One or more provided IDs do not exist. Please use valid lookup option IDs.",
        )
    await db.refresh(current_user)
    # Recompute compatibility score in background after every profile update
    background_tasks.add_task(_recompute_score_bg, current_user.id)
    return MeResponse.model_validate(current_user)


async def _recompute_score_bg(user_id):
    """Background task: open a fresh DB session and recompute the user's score."""
    try:
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as bg_db:
            user = await bg_db.get(User, user_id)
            if user:
                await compute_and_save_score(user, bg_db)
    except Exception as exc:
        _log.warning("Background score recompute failed for %s: %s", user_id, exc)


@router.post("/me/best-photo", response_model=MeResponse, summary="Reorder photos — best quality first")
async def select_best_photo(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """
    Downloads each of the user's uploaded photos, scores them using the
    lightweight quality check (blur + brightness), then saves the list
    reordered from best to worst quality.  The first photo becomes the
    profile picture shown on the discovery feed.
    """
    photos: list[str] = list(current_user.photos or [])

    if len(photos) < 2:
        # Nothing to reorder — return as-is
        return MeResponse.model_validate(current_user)

    scored: list[tuple[str, float]] = []

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for url in photos:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    _log.warning("best-photo: could not fetch %s (status %s)", url, resp.status_code)
                    scored.append((url, 0.0))
                    continue

                _, _, _, _, quality_score = await asyncio.to_thread(_check_quality, resp.content)
                _log.info("best-photo: %s → quality %.3f", url, quality_score)
                scored.append((url, quality_score))

            except Exception as exc:
                _log.warning("best-photo: error scoring %s: %s", url, exc)
                scored.append((url, 0.0))

    scored.sort(key=lambda x: x[1], reverse=True)
    reordered = [url for url, _ in scored]

    current_user.photos = reordered
    current_user.best_photo_enabled = True
    await db.commit()
    await db.refresh(current_user)

    _log.info(
        "best-photo: reordered %d photos for user %s | scores: %s",
        len(reordered), current_user.id,
        [(url.split("/")[-1][:20], round(s, 3)) for url, s in scored],
    )

    return MeResponse.model_validate(current_user)


@router.post("/me/push-token", summary="Register or update push notification token")
async def register_push_token(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """Store the device push token (Expo or FCM) for this user."""
    token = str(body.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=422, detail="token is required")

    current_user.push_token = token
    await db.commit()
    _log.info("push-token | user=%s token=%s…", current_user.id, token[:24])
    return {"ok": True}


# ── Public email availability check ──────────────────────────────────────────

class EmailCheckRequest(BaseModel):
    email: EmailStr


@router.post("/check-email", summary="Check if an email address is already registered")
async def check_email(
    body: EmailCheckRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns {"available": true} when the email is not yet taken.
    Returns {"available": false} when it is already registered.
    Does NOT require authentication — called during onboarding before the
    user has saved their email.
    """
    result = await db.execute(
        select(User.id).where(User.email == body.email)
    )
    taken = result.scalar_one_or_none() is not None
    return {"available": not taken}


@router.patch("/me/snooze", summary="Toggle snooze mode — hides profile from discovery")
async def toggle_snooze(
    current_user: User = Depends(get_current_user_allow_inactive),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Flips is_active for the current user.
    When is_active=False the user is invisible in the discover feed.
    Returns {"snoozed": bool} — true means profile is now hidden.
    """
    new_state = not current_user.is_active
    current_user.is_active = new_state
    db.add(current_user)
    await db.commit()
    return {"snoozed": not new_state, "is_active": new_state}
