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

from app.core.deps import get_current_user, get_current_user_allow_inactive, get_pro_user
from app.core.photo_analyzer import get_photo_quality_score
from app.db.session import get_db
from app.models.user import User
from app.schemas.profile import FilterUpdateRequest, MeResponse, ProfileUpdateRequest
from app.services.scoring import compute_and_save_score

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

# ── Fields that can NEVER be changed through any user-facing endpoint ─────────
# Even if they somehow appear in the request body (e.g. via raw API tweaks),
# the update handler will silently strip them and log a warning.
_IMMUTABLE_FIELDS: frozenset[str] = frozenset({
    "phone",       # identity credential — must go through OTP re-verification
    "apple_id",    # identity credential — set at sign-in only
    "id",          # primary key — never writable
})

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
    "city", "hometown", "living_in", "address", "country",
    # NOTE: latitude/longitude are intentionally excluded — coordinates must be
    # set only through POST /location/update (GPS) or POST /location/change-city
    # (travel mode). Direct coordinate injection via PATCH is not permitted.
    "voice_prompts", "work_experience", "education",
    "work_photos", "work_prompts", "work_matching_goals",
    "work_are_you_hiring", "work_commitment_level_id", "work_skills",
    "work_equity_split_id", "work_industries", "work_scheduling_url",
    "work_who_to_show_id", "work_priority_startup",
    "work_headline", "work_persona",
    "work_num_founders_id", "work_primary_role_id",
    "work_years_experience_id", "work_job_search_status_id",
    "linkedin_url",
    # Discover filter preferences
    "filter_age_min", "filter_age_max", "filter_max_distance_km",
    "filter_verified_only", "filter_star_signs", "filter_interests",
    "filter_languages", "filter_purpose", "filter_looking_for",
    "filter_education_level", "filter_family_plans", "filter_have_kids",
    "filter_ethnicities", "filter_exercise", "filter_drinking", "filter_smoking",
    "filter_height_min", "filter_height_max",
    # Mood status
    "mood_emoji", "mood_text",
    # Privacy + university
    "hide_age", "hide_distance", "university", "require_verified_to_chat",
    # Pro features
    "is_incognito", "travel_mode_enabled", "auto_zod_enabled",
    "travel_city", "travel_country",
    # Notification preferences
    "notif_new_match", "notif_new_message", "notif_super_like",
    "notif_liked_profile", "notif_profile_views", "notif_ai_picks",
    "notif_promotions", "notif_dating_tips",
    # Halal profile fields
    "sect_id", "prayer_frequency_id", "marriage_timeline_id",
    "wali_email", "wali_name", "wali_age", "wali_relation", "wali_verified",
    "blur_photos_halal", "halal_mode_enabled", "work_mode_enabled",
    # Halal filters
    "filter_sect", "filter_prayer_frequency", "filter_marriage_timeline",
    "filter_wali_verified_only", "filter_wants_to_work",
    # Religion filter (basic, free)
    "filter_religions",
})

_LIFESTYLE_KEYS: frozenset[str] = frozenset({"drinking", "smoking", "exercise", "diet"})

# ── Pro-only filter fields — hard-rejected for free users on PATCH ────────────
_PRO_ONLY_FILTER_FIELDS: frozenset[str] = frozenset({
    "filter_purpose", "filter_looking_for", "filter_education_level",
    "filter_family_plans", "filter_have_kids",
})

# ── Pro-only profile/feature fields — hard-rejected for free users on PATCH ───
# These control features that are exclusively part of the Pro tier.
# A free user posting these fields receives a 403 so the response is explicit.
_PRO_ONLY_PROFILE_FIELDS: frozenset[str] = frozenset({
    "is_incognito",
    "travel_mode_enabled",
    "travel_city",
    "travel_country",
    "auto_zod_enabled",
})


def _build_me(user: User) -> MeResponse:
    """Build a MeResponse, injecting computed fields that can't live in the schema."""
    r = MeResponse.model_validate(user)
    raw = user.push_token or ""
    r.has_push_token = bool(raw)
    return r


@router.get("/me", response_model=MeResponse, summary="Get current user profile")
async def get_me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return _build_me(current_user)


@router.patch("/me", response_model=MeResponse, summary="Update profile (onboarding or edit)")
async def update_me(
    payload: ProfileUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    update_data = payload.model_dump(exclude_unset=True)
    _log.info("PATCH /profile/me — fields: %s", list(update_data.keys()))

    # Hard-strip immutable identity fields — phone, apple_id, id can never be
    # changed via this endpoint, regardless of what the request body contains.
    attempted_immutable = _IMMUTABLE_FIELDS & update_data.keys()
    if attempted_immutable:
        _log.warning(
            "User %s attempted to update immutable field(s) %s — silently ignored.",
            current_user.id,
            attempted_immutable,
        )
        for f in attempted_immutable:
            del update_data[f]

    # Enforce Pro subscription for Pro-gated feature and filter fields.
    # Return explicit 403 so free users (and anyone bypassing the frontend)
    # receive a clear error rather than a silent no-op.
    if current_user.subscription_tier not in ("pro", "premium_plus"):
        # Feature fields — hard reject
        pro_feature_attempted = _PRO_ONLY_PROFILE_FIELDS & update_data.keys()
        if pro_feature_attempted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Field(s) {sorted(pro_feature_attempted)} require a Pro subscription. "
                    "Upgrade to unlock these features."
                ),
            )
        # Filter fields — hard reject
        pro_filter_attempted = _PRO_ONLY_FILTER_FIELDS & update_data.keys()
        if pro_filter_attempted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Filter(s) {sorted(pro_filter_attempted)} require a Pro subscription. "
                    "Upgrade to unlock advanced filters."
                ),
            )

    if not update_data:
        _log.warning(
            "PATCH /profile/me returned 422 — empty payload after model_dump. "
            "Raw payload keys: %s  User: %s",
            list(payload.model_dump(exclude_none=True).keys()),
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update.",
        )

    # Guard: halal_mode_enabled=True requires the user to have a religion set.
    # This prevents non-Muslim users from enabling halal mode via direct API calls.
    if update_data.get("halal_mode_enabled") is True:
        # Use the religion_id that would be active after this update, or the
        # existing one if the request is not also changing religion_id.
        effective_religion_id = update_data.get("religion_id", current_user.religion_id)
        if not effective_religion_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Halal mode is only available to Muslim users. Please set your religion on your profile first.",
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
        # Photo minimum enforcement — only block *removals* that drop below 2.
        # Allow adding photos freely (including going from 0→1 during re-setup).
        if field == "photos" and value is not None:
            filled = [u for u in value if u]
            existing_count = len(current_user.photos or [])
            if current_user.is_onboarded and len(filled) < 4 and len(filled) < existing_count:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Your profile must have at least 4 photos.",
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

        # When onboarding completes, validate required fields then gate on face verification.
        if field == "is_onboarded" and value is True:
            # ── Minimum 4 photos required ─────────────────────────────────────
            # Count photos from the update payload (if being set now) or existing
            photos_after = update_data.get("photos", current_user.photos) or []
            filled_photos = [p for p in photos_after if p]
            if len(filled_photos) < 4:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="You need at least 4 photos to complete your profile.",
                )

            # ── Religion required ─────────────────────────────────────────────
            religion_after = update_data.get("religion_id", current_user.religion_id)
            if not religion_after:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Please select your religion to complete your profile.",
                )

            # ── Immediately require face verification ─────────────────────────
            # The 423 gate in main.py blocks all other API calls until the user
            # passes face scan. face_scan_required is cleared on verification success.
            if current_user.verification_status != "verified":
                current_user.face_scan_required = True

        # When the user manually turns off travel mode, restore their real GPS location
        if field == "travel_mode_enabled" and value is False:
            current_user.travel_expires_at = None
            current_user.travel_city = None
            current_user.travel_country = None
            if current_user.real_latitude is not None:
                current_user.latitude = current_user.real_latitude
                current_user.longitude = current_user.real_longitude
                current_user.city = current_user.real_city
                current_user.country = current_user.real_country
            current_user.real_latitude = None
            current_user.real_longitude = None
            current_user.real_city = None
            current_user.real_country = None

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
    return _build_me(current_user)


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
        return _build_me(current_user)

    async def _score_photo(client: httpx.AsyncClient, url: str) -> tuple[str, float]:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                _log.warning("best-photo: could not fetch %s (status %s)", url, resp.status_code)
                return (url, 0.0)
            quality_score = await asyncio.to_thread(get_photo_quality_score, resp.content)
            _log.info("best-photo: %s → quality %.3f", url, quality_score)
            return (url, quality_score)
        except Exception as exc:
            _log.warning("best-photo: error scoring %s: %s", url, exc)
            return (url, 0.0)

    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        scored: list[tuple[str, float]] = list(
            await asyncio.gather(*[_score_photo(client, url) for url in photos])
        )

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

    return _build_me(current_user)


@router.get("/me/push-token", summary="Get current push token registration status")
async def get_push_token_status(
    current_user: User = Depends(get_current_user),
):
    """Returns whether the user has a valid FCM token registered."""
    raw = current_user.push_token or ""
    is_expo = raw.startswith("ExponentPushToken")
    return {
        "has_push_token": bool(raw),
        "token_type": "expo" if is_expo else ("fcm" if raw else None),
    }


@router.post("/me/push-token", summary="Register or update FCM push notification token")
async def register_push_token(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession    = Depends(get_db),
):
    """Store the device FCM token for push notifications.

    Replaces any previously stored token (including old Expo tokens).
    The token is stored in the push_token column and used by the backend
    to send FCM messages to this user's device.
    """
    token = str(body.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=422, detail="token is required")

    is_expo_token = token.startswith("ExponentPushToken")
    token_type    = "expo_legacy" if is_expo_token else "fcm"

    current_user.push_token = token
    await db.commit()
    _log.info(
        "push-token | user=%s type=%s token=%s…",
        current_user.id, token_type, token[:28],
    )
    return {"ok": True, "token_type": token_type}


# ── Notification channels ─────────────────────────────────────────────────────

# Channel definitions are served from here so the mobile app never needs a
# code update to get new channels — just bump the list below.
_NOTIFICATION_CHANNELS = [
    {
        "id": "activity",
        "name": "Activity",
        "description": "Matches, messages, likes and other activity",
        "importance": "high",
        "sound": True,
        "vibration": True,
        "badge": True,
    },
    {
        "id": "incoming_call",
        "name": "Incoming Calls",
        "description": "Incoming voice and video call alerts",
        "importance": "max",
        "sound": True,
        "vibration": True,
        "badge": False,
    },
    {
        "id": "marketing",
        "name": "Promotions & Updates",
        "description": "Feature updates, dating tips and promotions",
        "importance": "default",
        "sound": False,
        "vibration": False,
        "badge": False,
    },
]


@router.get("/notification-channels", summary="Push notification channel definitions for client-side registration")
async def get_notification_channels():
    """
    Returns the list of Android notification channels the app should register.
    The client checks each channel's existence before creating it (idempotent).
    No auth required — called on first app launch before sign-in.
    """
    return {"channels": _NOTIFICATION_CHANNELS}


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


_FILTER_FIELDS: frozenset[str] = frozenset({
    "filter_age_min", "filter_age_max", "filter_max_distance_km",
    "filter_verified_only", "filter_star_signs", "filter_interests", "filter_languages",
    "filter_religions",
    "filter_purpose", "filter_looking_for", "filter_education_level",
    "filter_family_plans", "filter_have_kids", "filter_ethnicities",
    "filter_exercise", "filter_drinking", "filter_smoking",
    "filter_height_min", "filter_height_max",
    # Halal-specific filters (free tier — available to all Muslims)
    "filter_sect", "filter_prayer_frequency", "filter_marriage_timeline",
    "filter_wali_verified_only", "filter_wants_to_work",
    # Work-mode filters (stored as single JSONB blob)
    "work_filter_settings",
})

_PRO_FILTER_FIELDS: frozenset[str] = frozenset({
    "filter_purpose", "filter_looking_for", "filter_education_level",
    "filter_family_plans", "filter_have_kids",
})


@router.patch("/me/filters", response_model=MeResponse, summary="Save discover filter preferences")
async def update_filters(
    payload: FilterUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """
    Dedicated endpoint for saving discover filter preferences.
    Only filter_* fields are accepted. Pro-only filters are silently
    ignored for free users.
    """
    data = payload.model_dump(exclude_unset=True)

    if not data:
        # Nothing sent — just return the current profile unchanged
        return _build_me(current_user)

    # Reject Pro-only filters for free users — explicit 403 so any client
    # attempting to bypass frontend checks gets a clear error.
    if current_user.subscription_tier not in ("pro", "premium_plus"):
        pro_attempted = _PRO_FILTER_FIELDS & data.keys()
        if pro_attempted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Filter(s) {sorted(pro_attempted)} require a Pro subscription. "
                    "Upgrade to unlock advanced filters."
                ),
            )

    for field, value in data.items():
        if field in _FILTER_FIELDS:
            # Distance: cap at 80 km — null (previously "Any") becomes 80.
            # This removes the unlimited/"Any" concept; 80 km is the hard max.
            if field == "filter_max_distance_km":
                if value is None:
                    value = 80
                else:
                    value = min(int(value), 80)
            setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)
    return _build_me(current_user)


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
