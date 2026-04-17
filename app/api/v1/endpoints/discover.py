"""
Discover endpoint — returns a paginated feed of profiles matching the current
user's saved discover filters.

Distance is calculated server-side using the Haversine formula in pure SQL so
we never need PostGIS.

GET  /discover/feed?page=0&limit=10&mode=date
POST /discover/swipe   body: {swiped_id, direction, mode}
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_score import UserScore
from app.models.subscription_plan import SubscriptionPlan
from app.services.scoring import compatibility_between, get_or_create_score, get_or_compute_compatibility, heuristic_score_obj

# ─── Plan feature helpers ──────────────────────────────────────────────────────

async def _get_feature_limit(tier: str, feature_key: str, db: AsyncSession, fallback: int = 5) -> int:
    """
    Look up a quantity-type feature limit from the canonical monthly plan
    for the given tier. Falls back to `fallback` if the plan or feature is
    not found.

    tier: "pro" | "premium_plus"
    feature_key: e.g. "super_likes", "profile_boosts"
    """
    tier_keyword = "Premium+" if tier == "premium_plus" else "Pro"
    result = await db.execute(
        select(SubscriptionPlan).where(
            SubscriptionPlan.is_active.is_(True),
            SubscriptionPlan.name.icontains(tier_keyword),
        ).order_by(SubscriptionPlan.sort_order.desc())
    )
    for plan in result.scalars().all():
        if plan.features:
            for feat in plan.features:
                if isinstance(feat, dict) and feat.get("key") == feature_key:
                    return int(feat.get("limit", fallback))
    return fallback

# Lazy import to avoid circular — resolved at call time
def _get_notify_manager():
    from app.api.v1.endpoints.chat import notify_manager
    return notify_manager

from app.core.push import send_push_notification, notify_user

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/discover", tags=["discover"])

# ── In-memory lookup cache ────────────────────────────────────────────────────
# Loaded once on first request to avoid repeated DB round-trips per profile.

_lookup_cache: dict[int, dict] = {}           # id → {category, emoji, label}
_rel_cache:    dict[int, dict] = {}           # id → {value, label}
_cache_loaded = False


async def _ensure_cache(db: AsyncSession) -> None:
    global _lookup_cache, _rel_cache, _cache_loaded
    if _cache_loaded:
        return
    rows = await db.execute(
        text("SELECT id, category, emoji, label FROM lookup_options WHERE is_active = true")
    )
    for row in rows.fetchall():
        _lookup_cache[row[0]] = {"category": row[1], "emoji": row[2], "label": row[3]}

    rrows = await db.execute(
        text("SELECT id, value, label FROM relationship_types")
    )
    for row in rrows.fetchall():
        _rel_cache[row[0]] = {"value": row[1], "label": row[2]}

    _cache_loaded = True


def _label(id_: int | None, emoji_prefix: bool = False) -> str | None:
    if id_ is None:
        return None
    item = _lookup_cache.get(id_)
    if not item:
        return None
    if emoji_prefix and item.get("emoji"):
        return f"{item['emoji']} {item['label']}"
    return item["label"]


def _labels(ids: list | None, emoji_prefix: bool = False) -> list[dict]:
    if not ids:
        return []
    out = []
    for entry in ids:
        # Accept both plain int IDs and {"id": N} dict format
        id_ = entry["id"] if isinstance(entry, dict) else entry
        item = _lookup_cache.get(id_)
        if item:
            out.append({"emoji": item.get("emoji", ""), "label": item["label"]})
    return out


def _rel_labels(ids: list | None) -> list[str]:
    if not ids:
        return []
    out = []
    for entry in ids:
        id_ = entry["id"] if isinstance(entry, dict) else entry
        if id_ in _rel_cache:
            out.append(_rel_cache[id_]["label"])
    return out


def _age(dob: date | None) -> int | None:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _format_height(cm: int | None) -> str | None:
    if not cm:
        return None
    feet = cm / 30.48
    ft = int(feet)
    inches = round((feet - ft) * 12)
    return f"{ft}'{inches}\" ({cm} cm)"


def _build_profile(u: User, distance_km: float | None, compat: dict | None = None) -> dict[str, Any]:
    """Convert a User ORM row into the discover profile dict for the frontend."""
    lifestyle_labels: dict[str, str] = {}
    if u.lifestyle:
        key_map = {"drinking": "drinks", "smoking": "smokes", "exercise": "exercise", "diet": "diet"}
        for trait, lbl_key in key_map.items():
            val_id = u.lifestyle.get(trait)
            if val_id is not None:
                lbl = _label(val_id)
                if lbl:
                    lifestyle_labels[lbl_key] = lbl

    age = _age(u.date_of_birth)
    dist_str = f"{round(distance_km, 1)} km" if distance_km is not None else None

    return {
        "id":         str(u.id),
        "name":       u.full_name,
        "age":        None if u.hide_age else age,
        "verified":   u.verification_status == "verified",
        "premium":    u.subscription_tier in ("pro", "premium_plus"),
        "location":   u.city,
        "distance":   None if u.hide_distance else dist_str,
        "university": u.university,
        "about":      u.bio,
        "images":     list(u.photos or []),
        "interests":  _labels(u.interests, emoji_prefix=False),
        "languages":  [x["label"] for x in _labels(u.languages)],
        "prompts":    list(u.prompts or []),
        "lookingFor": _label(u.looking_for_id),
        "details": {
            "height":    _format_height(u.height_cm),
            "gender":    _label(u.gender_id),
            "sign":      _label(u.star_sign_id, emoji_prefix=True),
            "religion":  _label(u.religion_id),
            "ethnicity": _label(u.ethnicity_id),
            "education": _label(u.education_level_id),
            "wantsKids": _label(u.family_plans_id),
            **lifestyle_labels,
        },
        "purpose": _rel_labels(u.purpose),
        "last_active_at": u.updated_at.isoformat() if u.updated_at else None,
        "has_voice": bool(u.voice_prompts),
        "voice_prompts": list(u.voice_prompts or []),
        "mood": {"emoji": u.mood_emoji, "text": u.mood_text} if u.mood_text else None,
        "compatibility": compat,  # {percent, tier, breakdown} or None
        "halal": {
            "sect":             _label(u.sect_id),
            "prayerFrequency":  _label(u.prayer_frequency_id),
            "marriageTimeline": _label(u.marriage_timeline_id),
            "waliVerified":     u.wali_verified,
            "blurPhotos":       u.blur_photos_halal,
            "halalMode":        u.halal_mode_enabled,
        },
        # Work profile fields
        "work_headline":  u.work_headline,
        "work_persona":   u.work_persona,
        "work_experience": list(u.work_experience or []),
        "education":      list(u.education or []),
        "work": {
            "matchingGoals":    [x["label"] for x in _labels(u.work_matching_goals)],
            "commitmentLevel":  _label(u.work_commitment_level_id),
            "equitySplit":      _label(u.work_equity_split_id),
            "industries":       [x["label"] for x in _labels(u.work_industries)],
            "skills":           [x["label"] for x in _labels(u.work_skills)],
            "areYouHiring":     u.work_are_you_hiring,
            "schedulingUrl":    u.work_scheduling_url,
            "linkedInUrl":      u.linkedin_url,
            "linkedInVerified": u.linkedin_verified,
            "prompts":          list(u.work_prompts or []),
            "photos":           list(u.work_photos or []),
        } if (u.work_mode_enabled or u.work_matching_goals or u.work_commitment_level_id or u.linkedin_url) else None,
    }


# ── Smart preference scoring ──────────────────────────────────────────────────

# Default soft max distance when user hasn't set one (used only for scoring, not filtering)
_DEFAULT_SOFT_MAX_KM = 80.0


def _age_from_dob(dob: date | None) -> int | None:
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _list_overlap_score(mine: list | None, theirs: list | None) -> float:
    """0.0–1.0 based on fraction of my list items found in their list."""
    if not mine:
        return 0.5  # no preference → neutral
    if not theirs:
        return 0.3  # they have nothing set → slight penalty
    def _to_id(x):
        if isinstance(x, int):
            return x
        if isinstance(x, dict):
            return int(x.get("id", 0))
        return int(x)

    mine_set   = set(_to_id(x) for x in mine)
    theirs_set = set(_to_id(x) for x in theirs)
    overlap    = len(mine_set & theirs_set)
    return min(1.0, overlap / len(mine_set))


def _preference_score(me: User, candidate: User, dist_km: float | None) -> float:
    """
    Composite soft-preference score (0.0–1.0) across seven dimensions.
    When a dimension has no data it defaults to 0.5 (neutral) so it doesn't
    penalise incomplete profiles.

    Dimension weights
    -----------------
    35 % – distance proximity
    15 % – height match
    15 % – age proximity
    10 % – shared interests
    10 % – lifestyle alignment (exercise / drinking / smoking)
     5 % – family plans match
     5 % – values overlap
     5 % – education level match
    """

    # ── 1. Distance (35 %) ────────────────────────────────────────────────────
    # Use the viewer's saved max distance as the reference ceiling.
    # If not set, fall back to _DEFAULT_SOFT_MAX_KM (50 km) so closer
    # profiles still rank above far-away ones by default.
    if dist_km is None:
        d_score = 0.5  # no location data — neutral
    else:
        ceiling = float(me.filter_max_distance_km or _DEFAULT_SOFT_MAX_KM)
        ratio   = dist_km / ceiling  # 0 = right next door, 1 = at limit, >1 = beyond
        if ratio <= 0.1:
            d_score = 1.00
        elif ratio <= 0.25:
            d_score = 0.90
        elif ratio <= 0.5:
            d_score = 0.75
        elif ratio <= 1.0:
            d_score = 0.55
        elif ratio <= 1.5:
            d_score = 0.35  # outside limit but still show (soft, not hard cut)
        else:
            d_score = 0.15

    # ── 2. Height (15 %) ──────────────────────────────────────────────────────
    h_score = 0.5  # neutral default
    if candidate.height_cm is not None and (me.filter_height_min or me.filter_height_max):
        h  = candidate.height_cm
        lo = me.filter_height_min or 0
        hi = me.filter_height_max or 9999
        if lo <= h <= hi:
            h_score = 1.0
        else:
            gap = max(lo - h, h - hi, 0)
            h_score = 0.80 if gap <= 5 else (0.55 if gap <= 10 else 0.25)

    # ── 3. Age proximity (15 %) ───────────────────────────────────────────────
    a_score     = 0.5
    cand_age    = _age_from_dob(candidate.date_of_birth)
    has_age_pref = me.filter_age_min is not None or me.filter_age_max is not None
    if cand_age is not None and has_age_pref:
        lo_a = me.filter_age_min or 18
        hi_a = me.filter_age_max or 99
        if lo_a <= cand_age <= hi_a:
            a_score = 1.0
        else:
            gap_a = max(lo_a - cand_age, cand_age - hi_a, 0)
            a_score = 0.75 if gap_a <= 2 else (0.50 if gap_a <= 5 else 0.20)

    # ── 4. Interests overlap (10 %) ───────────────────────────────────────────
    # Use viewer's filter if set; otherwise compare own interests vs candidate's
    interests_mine = me.filter_interests or me.interests
    i_score = _list_overlap_score(interests_mine, candidate.interests)

    # ── 5. Lifestyle alignment (10 %) ─────────────────────────────────────────
    # Each sub-dimension (exercise, drinking, smoking) contributes equally.
    def _lifestyle_val(u: User, key: str) -> int | None:
        d = u.lifestyle or {}
        v = d.get(key)
        return int(v) if v is not None else None

    lifestyle_dims: list[tuple[list | None, int | None]] = [
        (me.filter_exercise, _lifestyle_val(candidate, "exercise")),
        (me.filter_drinking, _lifestyle_val(candidate, "drinking")),
        (me.filter_smoking,  _lifestyle_val(candidate, "smoking")),
    ]
    ls_parts: list[float] = []
    for filt, cand_val in lifestyle_dims:
        if not filt:
            ls_parts.append(0.5)   # no preference → neutral
        elif cand_val is None:
            ls_parts.append(0.4)   # candidate hasn't set it
        elif cand_val in [int(x) for x in filt]:
            ls_parts.append(1.0)   # exact match
        else:
            ls_parts.append(0.1)   # mismatch
    ls_score = sum(ls_parts) / len(ls_parts)

    # ── 6. Family plans (5 %) ─────────────────────────────────────────────────
    fp_score = 0.5
    if me.filter_family_plans:
        if candidate.family_plans_id is None:
            fp_score = 0.4
        elif candidate.family_plans_id in [int(x) for x in me.filter_family_plans]:
            fp_score = 1.0
        else:
            fp_score = 0.1
    elif me.family_plans_id and candidate.family_plans_id:
        fp_score = 1.0 if me.family_plans_id == candidate.family_plans_id else 0.3

    # ── 7. Values overlap (5 %) ───────────────────────────────────────────────
    v_score = _list_overlap_score(me.values_list, candidate.values_list)

    # ── 8. Education level (5 %) ──────────────────────────────────────────────
    ed_score = 0.5
    if me.filter_education_level:
        if candidate.education_level_id is None:
            ed_score = 0.4
        elif candidate.education_level_id in [int(x) for x in me.filter_education_level]:
            ed_score = 1.0
        else:
            ed_score = 0.2
    elif me.education_level_id and candidate.education_level_id:
        ed_score = 1.0 if me.education_level_id == candidate.education_level_id else 0.4

    # ── Weighted composite ────────────────────────────────────────────────────
    return (
        0.35 * d_score   +
        0.15 * h_score   +
        0.15 * a_score   +
        0.10 * i_score   +
        0.10 * ls_score  +
        0.05 * fp_score  +
        0.05 * v_score   +
        0.05 * ed_score
    )


# ── Query builder ─────────────────────────────────────────────────────────────

def _resolve_origin_coords(me: User) -> tuple[float | None, float | None]:
    """
    Return the (lat, lon) that should be used as THIS user's origin for all
    distance calculations and distance-filter SQL.

    Priority:
      1. Travel city coordinates  — when travel_mode_enabled is True AND
         latitude/longitude are set (they are set by /location/change-city).
      2. Real GPS coordinates     — latitude/longitude from /location/update.
      3. None, None               — no location available at all.

    This guarantees the discover feed always filters from the correct origin
    regardless of whether real GPS was updated after travel mode was set.
    """
    if me.travel_mode_enabled and me.latitude is not None and me.longitude is not None:
        return float(me.latitude), float(me.longitude)
    if not me.travel_mode_enabled and me.latitude is not None and me.longitude is not None:
        return float(me.latitude), float(me.longitude)
    return None, None


async def _fetch_discover_profiles(
    me: User,
    db: AsyncSession,
    page: int,
    limit: int,
    mode: str = "date",
    halal: bool = False,
) -> list[dict]:
    await _ensure_cache(db)

    is_pro = me.subscription_tier in ("pro", "premium_plus")
    # bypass_location_filter is an admin flag that removes the distance filter when
    # the user hasn't explicitly set one. If the user explicitly chose a distance
    # (filter_max_distance_km is not None), always honour it — even for admins.
    _bypass_distance = getattr(me, 'bypass_location_filter', False) and me.filter_max_distance_km is None

    # ── Opposite-gender filter (straight platform) ────────────────────────────
    # Man (223) sees Women (224), Woman (224) sees Men (223).
    # Non-binary / prefer-not-to-say / unknown → no gender restriction applied.
    GENDER_MAN   = 223
    GENDER_WOMAN = 224
    opposite_gender_id: int | None = None
    if me.gender_id == GENDER_MAN:
        opposite_gender_id = GENDER_WOMAN
    elif me.gender_id == GENDER_WOMAN:
        opposite_gender_id = GENDER_MAN

    # ── Base: active, onboarded, has at least 1 photo, exclude self ──────────
    base_filters = [
        User.id != me.id,
        User.is_active.is_(True),
        User.is_onboarded.is_(True),
        User.photos.isnot(None),
    ]

    # Standard feed must not show Halal-only profiles.
    # Users with halal_mode_enabled=True have opted into the Halal community
    # and should only be discoverable in the Halal feed.
    if not halal:
        base_filters.append(
            (User.halal_mode_enabled.is_(False)) | (User.halal_mode_enabled.is_(None))
        )
    # Work mode is gender-neutral (co-founder / networking matching).
    if opposite_gender_id is not None and mode != "work":
        base_filters.append(User.gender_id == opposite_gender_id)

    stmt = (
        select(User)
        .where(*base_filters)
        .order_by(User.created_at.desc())
        .offset(page * limit)
        .limit(limit * 5)   # over-fetch so chip-away post-filters leave enough candidates
    )

    # ── Exclude already-swiped / already-matched profiles ────────────────────
    # Skipped for bypass users (e.g. the owner/tester account) so profiles
    # always reappear regardless of prior swipe history.
    if not _bypass_distance:
        swiped_result = await db.execute(
            text("SELECT swiped_id FROM swipes WHERE swiper_id = CAST(:uid AS uuid) AND mode = :mode")
            .bindparams(uid=str(me.id), mode=mode)
        )
        swiped_ids = [row[0] for row in swiped_result.fetchall()]
        if swiped_ids:
            stmt = stmt.where(User.id.not_in(swiped_ids))

        matched_result = await db.execute(
            text("""
                SELECT CASE
                    WHEN user1_id = CAST(:uid AS uuid) THEN user2_id
                    ELSE user1_id
                END AS other_id
                FROM matches
                WHERE user1_id = CAST(:uid AS uuid) OR user2_id = CAST(:uid AS uuid)
            """).bindparams(uid=str(me.id))
        )
        matched_ids = [row[0] for row in matched_result.fetchall()]
        if matched_ids:
            stmt = stmt.where(User.id.not_in(matched_ids))

        # ── Exclude blocked users (both directions) ───────────────────────────
        blocked_result = await db.execute(
            text("""
                SELECT blocked_id FROM user_blocks WHERE blocker_id = CAST(:uid AS uuid)
                UNION
                SELECT blocker_id FROM user_blocks WHERE blocked_id = CAST(:uid AS uuid)
            """).bindparams(uid=str(me.id))
        )
        blocked_ids = [row[0] for row in blocked_result.fetchall()]
        if blocked_ids:
            stmt = stmt.where(User.id.not_in(blocked_ids))

        # ── Exclude by device_id (survives account deletion + re-creation) ───
        # If a blocked user deletes their account and signs up again on the same
        # physical device, their new account inherits the same device_id and is
        # still hidden from this user's feed.
        blocked_devices_result = await db.execute(
            text("""
                SELECT blocked_device_id
                FROM user_blocks
                WHERE blocker_id = CAST(:uid AS uuid)
                  AND blocked_device_id IS NOT NULL
            """).bindparams(uid=str(me.id))
        )
        blocked_device_ids = [row[0] for row in blocked_devices_result.fetchall()]
        if blocked_device_ids and me.device_id not in blocked_device_ids:
            # Exclude anyone whose current device_id was ever blocked by this user
            stmt = stmt.where(
                (User.device_id.is_(None)) |
                (User.device_id.not_in(blocked_device_ids))
            )

    # ── Resolve origin: travel city (if active) or real GPS ──────────────────
    # This is the single source of truth for ALL distance filtering below.
    _origin_lat, _origin_lon = _resolve_origin_coords(me)

    # ── Distance filter pushed into SQL (Haversine bounding-box + exact check) ─
    # When we have an origin and a max_km cap is set, filter in SQL so we don't
    # fetch thousands of far-away rows only to drop them in Python.
    # null filter_max_distance_km defaults to 20 km (matches the client slider default).
    # bypass_location_filter=True (admin override) skips ALL distance filtering
    # so the user sees profiles from any location worldwide.
    _effective_max_km = float(me.filter_max_distance_km) if me.filter_max_distance_km is not None else 20.0
    if (
        not _bypass_distance
        and _origin_lat is not None
        and _origin_lon is not None
    ):
        _max_km = _effective_max_km
        stmt = stmt.where(
            text(
                # Haversine entirely in SQL — no PostGIS required.
                "latitude  IS NOT NULL AND longitude IS NOT NULL AND "
                "2 * 6371 * ASIN(SQRT("
                "  POWER(SIN(RADIANS(latitude  - :lat) / 2), 2) + "
                "  COS(RADIANS(:lat)) * COS(RADIANS(latitude)) * "
                "  POWER(SIN(RADIANS(longitude - :lon) / 2), 2)"
                ")) <= :max_km"
            ).bindparams(lat=_origin_lat, lon=_origin_lon, max_km=_max_km)
        )

    # Work mode: only show users who have explicitly enabled work mode
    if mode == "work":
        stmt = stmt.where(User.work_mode_enabled.is_(True))

    # ── Work-mode discover filters ────────────────────────────────────────────
    # Applied when mode=work and the user has saved work filter preferences.
    if mode == "work" and me.work_filter_settings:
        wf = me.work_filter_settings  # dict

        # Distance (overrides the global filter_max_distance_km for work mode)
        wf_distance = wf.get("distance_km")
        if (
            wf_distance is not None
            and not _bypass_distance
            and _origin_lat is not None
            and _origin_lon is not None
        ):
            wf_max_km = float(wf_distance)
            stmt = stmt.where(
                text(
                    "latitude IS NOT NULL AND longitude IS NOT NULL AND "
                    "2 * 6371 * ASIN(SQRT("
                    "  POWER(SIN(RADIANS(latitude  - :wlat) / 2), 2) + "
                    "  COS(RADIANS(:wlat)) * COS(RADIANS(latitude)) * "
                    "  POWER(SIN(RADIANS(longitude - :wlon) / 2), 2)"
                    ")) <= :wmaxkm"
                ).bindparams(wlat=_origin_lat, wlon=_origin_lon, wmaxkm=wf_max_km)
            )

        # Verified-only
        if wf.get("verified_only"):
            stmt = stmt.where(User.verification_status == "verified")

        # Actively hiring only
        if wf.get("hiring_only"):
            stmt = stmt.where(User.work_are_you_hiring.is_(True))

        # Priority startup experience
        if wf.get("priority_startup"):
            stmt = stmt.where(User.work_priority_startup.is_(True))

        # Industries (at least one overlap)
        wf_industries = [int(i) for i in wf.get("industries", []) if str(i).isdigit()] if wf.get("industries") else []
        if wf_industries:
            ids_lit = ",".join(str(i) for i in wf_industries)
            stmt = stmt.where(
                text(
                    f"EXISTS (SELECT 1 FROM jsonb_array_elements(work_industries) _wi"
                    f" WHERE (_wi)::int = ANY(ARRAY[{ids_lit}]))"
                )
            )

        # Skills (at least one overlap)
        wf_skills = [int(i) for i in wf.get("skills", []) if str(i).isdigit()] if wf.get("skills") else []
        if wf_skills:
            ids_lit = ",".join(str(i) for i in wf_skills)
            stmt = stmt.where(
                text(
                    f"EXISTS (SELECT 1 FROM jsonb_array_elements(work_skills) _ws"
                    f" WHERE (_ws)::int = ANY(ARRAY[{ids_lit}]))"
                )
            )

        # Commitment level (match any selected)
        wf_commitment = [int(i) for i in wf.get("commitment_levels", []) if str(i).isdigit()] if wf.get("commitment_levels") else []
        if wf_commitment:
            stmt = stmt.where(User.work_commitment_level_id.in_(wf_commitment))

        # Who to see (work_who_to_show_id)
        wf_who = [int(i) for i in wf.get("who_to_see", []) if str(i).isdigit()] if wf.get("who_to_see") else []
        if wf_who:
            stmt = stmt.where(User.work_who_to_show_id.in_(wf_who))

        # Job search status (match any selected)
        wf_job_status = [int(i) for i in wf.get("job_search_statuses", []) if str(i).isdigit()] if wf.get("job_search_statuses") else []
        if wf_job_status:
            stmt = stmt.where(User.work_job_search_status_id.in_(wf_job_status))

        # Years of experience (match any selected)
        wf_yoe = [int(i) for i in wf.get("years_experience", []) if str(i).isdigit()] if wf.get("years_experience") else []
        if wf_yoe:
            stmt = stmt.where(User.work_years_experience_id.in_(wf_yoe))

        # ── Pro-only work filters ─────────────────────────────────────────────
        if is_pro:
            # Matching goals (at least one overlap)
            wf_goals = [int(i) for i in wf.get("matching_goals", []) if str(i).isdigit()] if wf.get("matching_goals") else []
            if wf_goals:
                ids_lit = ",".join(str(i) for i in wf_goals)
                stmt = stmt.where(
                    text(
                        f"EXISTS (SELECT 1 FROM jsonb_array_elements(work_matching_goals) _wg"
                        f" WHERE (_wg)::int = ANY(ARRAY[{ids_lit}]))"
                    )
                )

            # Equity split preference (match any selected)
            wf_equity = [int(i) for i in wf.get("equity_prefs", []) if str(i).isdigit()] if wf.get("equity_prefs") else []
            if wf_equity:
                stmt = stmt.where(User.work_equity_split_id.in_(wf_equity))

            # Startup stage
            wf_stages = [int(i) for i in wf.get("stages", []) if str(i).isdigit()] if wf.get("stages") else []
            if wf_stages:
                stmt = stmt.where(User.work_stage_id.in_(wf_stages))

            # Primary role
            wf_roles = [int(i) for i in wf.get("roles", []) if str(i).isdigit()] if wf.get("roles") else []
            if wf_roles:
                stmt = stmt.where(User.work_primary_role_id.in_(wf_roles))

            # Number of founders
            wf_founders = [int(i) for i in wf.get("num_founders", []) if str(i).isdigit()] if wf.get("num_founders") else []
            if wf_founders:
                stmt = stmt.where(User.work_num_founders_id.in_(wf_founders))

    # ── Verified-only filter (face-verified profiles only) ───────────────────
    # is_verified=True is given to ALL phone-signed-in users so it can't be
    # used here.  verification_status='verified' means the user passed face
    # verification and is the correct signal for the "Verified only" filter.
    if me.filter_verified_only:
        stmt = stmt.where(User.verification_status == "verified")

    # ── Age filter ────────────────────────────────────────────────────────────
    if me.filter_age_min or me.filter_age_max:
        today = date.today()
        if me.filter_age_max:
            max_age = me.filter_age_max
            min_dob = date(today.year - max_age - 1, today.month, today.day)
            # Use > (not >=) to exclude people who are already max_age+1 today
            stmt = stmt.where(User.date_of_birth > min_dob)
        if me.filter_age_min:
            min_age = me.filter_age_min
            max_dob = date(today.year - min_age, today.month, today.day)
            stmt = stmt.where(User.date_of_birth <= max_dob)

    # ── Religion filter (standard mode) ──────────────────────────────────────
    if me.filter_religions:
        stmt = stmt.where(User.religion_id.in_(me.filter_religions))

    # ── Star sign filter ──────────────────────────────────────────────────────
    if me.filter_star_signs:
        stmt = stmt.where(User.star_sign_id.in_(me.filter_star_signs))

    # ── Interests filter (at least one overlapping interest) ─────────────────
    # Stored as [{"id": N}, ...] so we unnest and compare the integer id field.
    if me.filter_interests:
        ids_literal = ",".join(str(i) for i in me.filter_interests)
        stmt = stmt.where(
            text(
                f"EXISTS ("
                f"  SELECT 1 FROM jsonb_array_elements(interests) _e"
                f"  WHERE (_e->>'id')::int = ANY(ARRAY[{ids_literal}])"
                f")"
            )
        )

    # ── Language filter ───────────────────────────────────────────────────────
    if me.filter_languages:
        ids_literal = ",".join(str(i) for i in me.filter_languages)
        stmt = stmt.where(
            text(
                f"EXISTS ("
                f"  SELECT 1 FROM jsonb_array_elements(languages) _e"
                f"  WHERE (_e->>'id')::int = ANY(ARRAY[{ids_literal}])"
                f")"
            )
        )

    # ── Ethnicity filter ──────────────────────────────────────────────────────
    if me.filter_ethnicities:
        stmt = stmt.where(User.ethnicity_id.in_(me.filter_ethnicities))

    # ── Exercise filter ───────────────────────────────────────────────────────
    if me.filter_exercise:
        stmt = stmt.where(
            text("(lifestyle->>'exercise')::int = ANY(ARRAY[" + ",".join(str(i) for i in me.filter_exercise) + "])")
        )

    # ── Drinking filter ───────────────────────────────────────────────────────
    if me.filter_drinking:
        stmt = stmt.where(
            text("(lifestyle->>'drinking')::int = ANY(ARRAY[" + ",".join(str(i) for i in me.filter_drinking) + "])")
        )

    # ── Smoking filter ────────────────────────────────────────────────────────
    if me.filter_smoking:
        stmt = stmt.where(
            text("(lifestyle->>'smoking')::int = ANY(ARRAY[" + ",".join(str(i) for i in me.filter_smoking) + "])")
        )

    # ── Height filter ──────────────────────────────────────────────────────────
    if me.filter_height_min:
        stmt = stmt.where(User.height_cm >= me.filter_height_min)
    if me.filter_height_max:
        stmt = stmt.where(User.height_cm <= me.filter_height_max)

    # ── Pro filters ───────────────────────────────────────────────────────────
    if is_pro:
        if me.filter_purpose:
            ids_literal = ",".join(str(i) for i in me.filter_purpose)
            stmt = stmt.where(
                text(
                    f"EXISTS ("
                    f"  SELECT 1 FROM jsonb_array_elements(purpose) _e"
                    f"  WHERE (_e->>'id')::int = ANY(ARRAY[{ids_literal}])"
                    f")"
                )
            )
        if me.filter_looking_for:
            stmt = stmt.where(User.looking_for_id.in_(me.filter_looking_for))
        if me.filter_education_level:
            stmt = stmt.where(User.education_level_id.in_(me.filter_education_level))
        if me.filter_family_plans:
            stmt = stmt.where(User.family_plans_id.in_(me.filter_family_plans))
        if me.filter_have_kids:
            stmt = stmt.where(User.have_kids_id.in_(me.filter_have_kids))

    # ── Halal mode filters ────────────────────────────────────────────────────
    # When halal=True, restrict the feed to same-religion profiles and apply
    # the user's saved halal-specific filter preferences.
    if halal:
        # Guard: user must have halal_mode_enabled set on their profile.
        # We trust this flag because it can only be set if the user is Muslim
        # (validated at the profile PATCH level). This avoids a fragile
        # cache-lookup that can fail after server restarts and cause false 403s.
        if not me.halal_mode_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Enable Halal mode on your profile to access the Halal feed.",
            )

        # Match same religion as current user
        stmt = stmt.where(User.religion_id == me.religion_id)

        # Only show profiles who have halal_mode_enabled = True
        stmt = stmt.where(User.halal_mode_enabled.is_(True))

        # Sect filter
        if me.filter_sect:
            stmt = stmt.where(User.sect_id.in_(me.filter_sect))

        # Prayer frequency filter
        if me.filter_prayer_frequency:
            stmt = stmt.where(User.prayer_frequency_id.in_(me.filter_prayer_frequency))

        # Marriage timeline filter
        if me.filter_marriage_timeline:
            stmt = stmt.where(User.marriage_timeline_id.in_(me.filter_marriage_timeline))

        # Wali verified filter
        if me.filter_wali_verified_only:
            stmt = stmt.where(User.wali_verified.is_(True))

        # Wants to work filter (True = must work, False = must not work, None = no pref)
        if me.filter_wants_to_work is True:
            stmt = stmt.where(
                User.work_matching_goals.isnot(None) | User.work_commitment_level_id.isnot(None)
            )
        elif me.filter_wants_to_work is False:
            stmt = stmt.where(
                User.work_matching_goals.is_(None) & User.work_commitment_level_id.is_(None)
            )

    result = await db.execute(stmt)
    candidates: list[User] = list(result.scalars().all())

    # ── Distance calculation (Python-side, for display only) ─────────────────
    # SQL already excluded out-of-range profiles when coordinates are available.
    # me_lat / me_lon come from _resolve_origin_coords above so they always
    # reflect travel city when travel_mode_enabled, otherwise real GPS.
    me_lat = _origin_lat
    me_lon = _origin_lon
    max_km = _effective_max_km  # default 20 when user hasn't set a preference

    has_my_location = me_lat is not None and me_lon is not None

    # Extract candidate IDs before any potential rollback, because rollback()
    # expires all tracked ORM objects and accessing .id afterwards triggers a
    # sync lazy-load which crashes in async asyncpg (MissingGreenlet).
    candidate_ids = [u.id for u in candidates]

    # ── Compatibility scores ──────────────────────────────────────────────────
    _had_rollback = False
    try:
        my_score = await get_or_create_score(me, db)
    except Exception:
        await db.rollback()
        _had_rollback = True
        # Rollback expires every attribute on `me`. Refresh so heuristic_score_obj
        # (sync, accesses many User columns) can read them without MissingGreenlet.
        await db.refresh(me)
        my_score = heuristic_score_obj(me)  # type: ignore[assignment]
    score_rows = {}
    if candidate_ids:
        try:
            score_result = await db.execute(
                select(UserScore).where(UserScore.user_id.in_(candidate_ids))
            )
            for sr in score_result.scalars().all():
                score_rows[sr.user_id] = sr
        except Exception:
            await db.rollback()
            _had_rollback = True

    # ── Reload `me` and candidates after any rollback ────────────────────────
    # db.rollback() expires ALL tracked ORM objects. If we then access any
    # attribute (e.g. u.latitude, me.filter_max_distance_km) inside a plain
    # sync loop, SQLAlchemy tries to lazy-load via asyncpg → MissingGreenlet.
    # Fix: after any rollback, reload `me` once and all candidates in one batch
    # query so every attribute is populated before the sync ranking loop below.
    if _had_rollback and candidate_ids:
        try:
            fresh = await db.execute(select(User).where(User.id.in_(candidate_ids)))
            candidates = list(fresh.scalars().all())
        except Exception:
            pass
    if _had_rollback:
        try:
            await db.refresh(me)
        except Exception:
            pass

    # ── Collect valid candidates, compute smart ranking score ─────────────────
    # smart_score = 60% compatibility + 40% multi-dim soft preferences.
    # Soft preferences include distance, height, age, interests, lifestyle,
    # family plans, values and education — weighted inside _preference_score().
    # Hard distance filter is still enforced here (SQL bounding-box already
    # narrowed candidates; this is an exact Haversine confirmation pass).
    ranked: list[tuple[float, dict]] = []
    for u in candidates:
        dist_km: float | None = None
        if has_my_location and u.latitude is not None and u.longitude is not None:
            dist_km = _haversine_km(me_lat, me_lon, u.latitude, u.longitude)
            if not _bypass_distance and max_km is not None and dist_km > max_km:
                continue
        elif not _bypass_distance and max_km is not None and has_my_location and (u.latitude is None or u.longitude is None):
            continue

        their_score = score_rows.get(u.id) or heuristic_score_obj(u)
        compat = compatibility_between(my_score, their_score)
        pref   = _preference_score(me, u, dist_km)

        # Blend: compat is a dict with "percent" (0–100), pref is 0–1
        compat_pct = compat.get("percent", 0) if isinstance(compat, dict) else (compat or 0)
        compat_norm = compat_pct / 100.0 if compat_pct > 1 else compat_pct
        smart = 0.60 * compat_norm + 0.40 * pref

        ranked.append((smart, _build_profile(u, dist_km, compat)))

    # Sort by smart score descending so best matches surface first
    ranked.sort(key=lambda x: x[0], reverse=True)

    return [profile for _, profile in ranked[:limit]]


# ── Schemas ───────────────────────────────────────────────────────────────────

class SwipeRequest(BaseModel):
    swiped_id: str
    direction: str          # "left", "right", or "super"
    mode: str = "date"      # "date" or "work"
    ice_breaker: str | None = None  # optional note sent with the swipe


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/swipe", summary="Record a swipe left or right on a profile")
async def record_swipe(
    body: SwipeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.direction not in ("left", "right", "super"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="direction must be 'left', 'right', or 'super'")
    if body.mode not in ("date", "work"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="mode must be 'date' or 'work'")

    FREE_DAILY_LIKE_LIMIT    = 20
    DAILY_WORK_CONNECT_LIMIT = 20

    # ── Daily like gate: free-tier users are capped at 20 right-swipes per day ─
    is_free = current_user.subscription_tier == "free"
    if is_free and body.direction in ("right", "super") and body.mode != "work":
        now_utc = datetime.now(timezone.utc)
        dl_reset = current_user.daily_likes_reset_at
        # Auto-reset when UTC day has rolled over (or never been set)
        if dl_reset is None or dl_reset.date() < now_utc.date():
            await db.execute(
                text(
                    "UPDATE users SET daily_likes_used = 0, daily_likes_reset_at = :now "
                    "WHERE id = CAST(:uid AS uuid)"
                ).bindparams(now=now_utc, uid=str(current_user.id))
            )
            current_user.daily_likes_used = 0
            current_user.daily_likes_reset_at = now_utc
        if current_user.daily_likes_used >= FREE_DAILY_LIKE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You've used all {FREE_DAILY_LIKE_LIMIT} free likes for today. Upgrade to Pro for unlimited likes.",
                headers={"X-Error-Code": "daily_limit_reached"},
            )
        # Atomic increment to prevent race condition with concurrent rapid swipes
        result = await db.execute(
            text(
                "UPDATE users SET daily_likes_used = daily_likes_used + 1 "
                "WHERE id = CAST(:uid AS uuid) AND daily_likes_used < :limit "
                "RETURNING daily_likes_used"
            ).bindparams(uid=str(current_user.id), limit=FREE_DAILY_LIKE_LIMIT)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            # Another concurrent request already hit the limit
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You've used all {FREE_DAILY_LIKE_LIMIT} free likes for today. Upgrade to Pro for unlimited likes.",
                headers={"X-Error-Code": "daily_limit_reached"},
            )
        current_user.daily_likes_used = updated

    # ── Daily work connect gate: all users capped at 20 connects per day ────────
    if body.mode == "work" and body.direction in ("right", "super"):
        now_utc  = datetime.now(timezone.utc)
        wc_reset = current_user.daily_work_connects_reset_at
        if wc_reset is None or wc_reset.date() < now_utc.date():
            await db.execute(
                text(
                    "UPDATE users SET daily_work_connects_used = 0, daily_work_connects_reset_at = :now "
                    "WHERE id = CAST(:uid AS uuid)"
                ).bindparams(now=now_utc, uid=str(current_user.id))
            )
            current_user.daily_work_connects_used = 0
            current_user.daily_work_connects_reset_at = now_utc
        if current_user.daily_work_connects_used >= DAILY_WORK_CONNECT_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You've used all {DAILY_WORK_CONNECT_LIMIT} work connects for today. Resets at midnight UTC.",
                headers={"X-Error-Code": "work_connect_limit_reached"},
            )
        # Atomic increment to prevent race condition
        wc_result = await db.execute(
            text(
                "UPDATE users SET daily_work_connects_used = daily_work_connects_used + 1 "
                "WHERE id = CAST(:uid AS uuid) AND daily_work_connects_used < :limit "
                "RETURNING daily_work_connects_used"
            ).bindparams(uid=str(current_user.id), limit=DAILY_WORK_CONNECT_LIMIT)
        )
        wc_updated = wc_result.scalar_one_or_none()
        if wc_updated is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You've used all {DAILY_WORK_CONNECT_LIMIT} work connects for today. Resets at midnight UTC.",
                headers={"X-Error-Code": "work_connect_limit_reached"},
            )
        current_user.daily_work_connects_used = wc_updated

    # ── Super-like gate: Pro subscribers only, 10 per calendar month ─────────
    if body.direction == "super":
        if current_user.subscription_tier not in ("pro", "premium_plus"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super likes are a Pro feature. Upgrade to send super likes.",
            )

        now      = datetime.now(timezone.utc)
        reset_at = current_user.super_likes_reset_at

        # Get limit from the subscription plan in DB (weekly period)
        sl_limit = await _get_feature_limit(
            current_user.subscription_tier, "super_likes", db, fallback=5
        )

        # Reset every 7 days (weekly)
        need_reset = (
            reset_at is None
            or (now - reset_at).total_seconds() >= 7 * 24 * 3600
        )
        if need_reset:
            current_user.super_likes_remaining = sl_limit
            current_user.super_likes_reset_at  = now

        if current_user.super_likes_remaining <= 0:
            next_reset  = (reset_at + timedelta(days=7)) if reset_at else now
            next_str    = next_reset.strftime("%-d %b")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No super likes remaining. Your next {sl_limit} drop on {next_str}.",
            )

        current_user.super_likes_remaining -= 1
        db.add(current_user)

    # Verify the swiped profile still exists — it may have been deleted while
    # the card was on-screen (or the client has a stale local cache).
    target_exists = await db.execute(
        select(User.id).where(User.id == body.swiped_id)
    )
    if target_exists.scalar_one_or_none() is None:
        # Profile no longer exists — silently accept so the client can clear
        # its local cache without crashing.
        return {"match": False, "swiped_id": body.swiped_id, "stale": True}

    # Upsert — if they somehow swipe twice, update direction without error
    await db.execute(
        text("""
            INSERT INTO swipes (swiper_id, swiped_id, direction, mode)
            VALUES (CAST(:swiper AS uuid), CAST(:swiped AS uuid), :direction, :mode)
            ON CONFLICT (swiper_id, swiped_id, mode)
            DO UPDATE SET direction = EXCLUDED.direction,
                          created_at = NOW()
        """).bindparams(
            swiper=str(current_user.id),
            swiped=body.swiped_id,
            direction=body.direction,
            mode=body.mode,
        )
    )

    # If right-swipe or super-like: check if the other person has also swiped right → it's a match
    is_match = False
    is_super = body.direction == "super"
    if body.direction in ("right", "super"):
        # Also record in likes table for the "liked you" feature
        await db.execute(
            text("""
                INSERT INTO likes (liker_id, liked_id)
                VALUES (CAST(:swiper AS uuid), CAST(:swiped AS uuid))
                ON CONFLICT DO NOTHING
            """).bindparams(swiper=str(current_user.id), swiped=body.swiped_id)
        )

        result = await db.execute(
            text("""
                SELECT 1 FROM swipes
                WHERE swiper_id = CAST(:swiped AS uuid)
                  AND swiped_id = CAST(:swiper AS uuid)
                  AND direction IN ('right', 'super')
                  AND mode = :mode
                LIMIT 1
            """).bindparams(
                swiped=body.swiped_id,
                swiper=str(current_user.id),
                mode=body.mode,
            )
        )
        is_match = result.first() is not None

        if is_match:
            # Persist match (stable ordering: smaller UUID first)
            u1, u2 = sorted([str(current_user.id), body.swiped_id])
            await db.execute(
                text("""
                    INSERT INTO matches (user1_id, user2_id)
                    VALUES (CAST(:u1 AS uuid), CAST(:u2 AS uuid))
                    ON CONFLICT DO NOTHING
                """).bindparams(u1=u1, u2=u2)
            )

    await db.commit()

    # ── Real-time push via WebSocket notify channel ───────────────────────────
    if body.direction in ("right", "super"):
        await _ensure_cache(db)
        nm = _get_notify_manager()
        liker_profile = _build_profile(current_user, None)

        if is_match:
            # Fetch the swiped user once — needed for WS + push
            swiped_result = await db.execute(select(User).where(User.id == body.swiped_id))
            swiped_user = swiped_result.scalar_one_or_none()

            # ── WebSocket: real-time match event to both users ────────────────
            match_payload = {"type": "match", "profile": liker_profile}
            await nm.send_to(body.swiped_id, match_payload)
            if swiped_user:
                await nm.send_to(
                    str(current_user.id),
                    {"type": "match", "profile": _build_profile(swiped_user, None)},
                )

            # ── Push notification: for users not on the WS (background/killed) ─
            liker_name   = current_user.full_name or "Someone"
            liker_image  = (current_user.photos or [None])[0] if current_user.photos else None
            swiped_name  = swiped_user.full_name if swiped_user else "Someone"
            swiped_image = (swiped_user.photos or [None])[0] if (swiped_user and swiped_user.photos) else None

            # Notify the person who was swiped on
            if swiped_user:
                await notify_user(
                    swiped_user, "match",
                    title="It's a Match! 🎉",
                    body=f"You and {liker_name} liked each other. Say hi!",
                    data={
                        "type":          "match",
                        "other_user_id": str(current_user.id),
                        "other_name":    liker_name,
                        "other_image":   liker_image,
                    },
                )
            # Notify the swiper (may have already left the app)
            await notify_user(
                current_user, "match",
                title="It's a Match! 🎉",
                body=f"You and {swiped_name} liked each other. Say hi!",
                data={
                    "type":          "match",
                    "other_user_id": body.swiped_id,
                    "other_name":    swiped_name,
                    "other_image":   swiped_image,
                },
            )
        else:
            # Push liked_you (or super_like) event to the person being liked
            event_type = "super_like" if is_super else "liked_you"
            payload: dict[str, Any] = {"type": event_type, "profile": liker_profile}
            if is_super and body.ice_breaker:
                payload["ice_breaker"] = body.ice_breaker
            await nm.send_to(body.swiped_id, payload)

            # Push notification for liked_you / super_like (backgrounded user)
            liker_name = current_user.full_name or "Someone"
            swiped_result = await db.execute(select(User).where(User.id == body.swiped_id))
            swiped_user   = swiped_result.scalar_one_or_none()
            if swiped_user:
                if is_super:
                    await notify_user(
                        swiped_user, "super_like",
                        title=f"⭐ {liker_name} super-liked you!",
                        body="Open the app to see who it is.",
                        data={"type": "super_like", "other_user_id": str(current_user.id)},
                    )
                else:
                    await notify_user(
                        swiped_user, "liked_you",
                        title=f"❤️ {liker_name} liked you!",
                        body="Open the app to see who it is.",
                        data={"type": "liked_you", "other_user_id": str(current_user.id)},
                    )

    # Compute daily likes remaining for free users to return to client
    daily_likes_remaining: int | None = None
    if is_free and body.direction in ("right", "super") and body.mode != "work":
        daily_likes_remaining = max(0, FREE_DAILY_LIKE_LIMIT - current_user.daily_likes_used)

    # Always return work connect counts when in work mode
    work_connects_used      = current_user.daily_work_connects_used if body.mode == "work" else None
    work_connects_remaining = max(0, DAILY_WORK_CONNECT_LIMIT - current_user.daily_work_connects_used) if body.mode == "work" else None

    return {
        "recorded": True,
        "match": is_match,
        "super": is_super,
        "super_likes_remaining":  current_user.super_likes_remaining if is_super else None,
        "daily_likes_remaining":  daily_likes_remaining,
        "daily_likes_limit":      FREE_DAILY_LIKE_LIMIT if is_free else None,
        "work_connects_used":     work_connects_used,
        "work_connects_remaining": work_connects_remaining,
        "work_connects_limit":    DAILY_WORK_CONNECT_LIMIT if body.mode == "work" else None,
    }


@router.get("/profile/{user_id}", summary="View a matched user's public profile")
async def get_match_profile(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the discover-style profile dict for a matched user, including compatibility."""
    import uuid as _uuid
    await _ensure_cache(db)
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id")

    result = await db.execute(select(User).where(User.id == uid))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Distance
    distance_km: float | None = None
    if (current_user.latitude is not None and current_user.longitude is not None
            and target.latitude is not None and target.longitude is not None):
        distance_km = _haversine_km(
            float(current_user.latitude), float(current_user.longitude),
            float(target.latitude),       float(target.longitude),
        )

    # Compatibility — cached in user_compatibility table, only recomputed when
    # either user's profile has changed (detected by profile_hash comparison).
    compat: dict | None = None
    try:
        compat = await get_or_compute_compatibility(current_user, target, db)
    except Exception:
        _log.warning("Could not compute compatibility for %s ↔ %s", current_user.id, uid)

    return _build_profile(target, distance_km, compat)


@router.get("/counts", summary="Get badge counts for the current user")
async def get_counts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Returns liked_you, matches, views (swipes received) counts."""
    uid = str(current_user.id)

    liked_row = await db.execute(
        text("""
            SELECT COUNT(*) FROM likes l
            WHERE l.liked_id = CAST(:uid AS uuid)
              AND NOT EXISTS (
                  SELECT 1 FROM matches m
                  WHERE (m.user1_id = l.liker_id AND m.user2_id = CAST(:uid AS uuid))
                     OR (m.user2_id = l.liker_id AND m.user1_id = CAST(:uid AS uuid))
              )
        """).bindparams(uid=uid)
    )
    liked_count = liked_row.scalar() or 0

    matches_row = await db.execute(
        text("SELECT COUNT(*) FROM matches WHERE user1_id = CAST(:uid AS uuid) OR user2_id = CAST(:uid AS uuid)")
        .bindparams(uid=uid)
    )
    matches_count = matches_row.scalar() or 0

    views_row = await db.execute(
        text("SELECT COUNT(*) FROM swipes WHERE swiped_id = CAST(:uid AS uuid)").bindparams(uid=uid)
    )
    views_count = views_row.scalar() or 0

    return {
        "liked_you": int(liked_count),
        "matches": int(matches_count),
        "views": int(views_count),
        "unread_chats": int(matches_count),
    }


@router.get("/liked-you", summary="Get profiles of people who liked the current user")
async def get_liked_you(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Returns up to 50 profiles that have liked the current user (most recent first).
    No filters applied — if they liked you, they show up regardless of distance, age, or gender.
    Distance from the viewer is calculated and included in the profile.
    """
    await _ensure_cache(db)
    uid = str(current_user.id)

    rows = await db.execute(
        text("""
            SELECT l.liker_id,
                   COALESCE(s.direction = 'super', false) AS is_super
            FROM likes l
            LEFT JOIN swipes s ON s.swiper_id = l.liker_id
                               AND s.swiped_id = CAST(:uid AS uuid)
                               AND s.direction = 'super'
                               AND s.mode = 'date'
            WHERE l.liked_id = CAST(:uid AS uuid)
              AND NOT EXISTS (
                  SELECT 1 FROM matches m
                  WHERE (m.user1_id = l.liker_id AND m.user2_id = CAST(:uid AS uuid))
                     OR (m.user2_id = l.liker_id AND m.user1_id = CAST(:uid AS uuid))
              )
              AND NOT EXISTS (
                  SELECT 1 FROM swipes sw
                  WHERE sw.swiper_id = CAST(:uid AS uuid)
                    AND sw.swiped_id = l.liker_id
              )
            ORDER BY l.created_at DESC
            LIMIT 50
        """).bindparams(uid=uid)
    )
    rows_data = rows.fetchall()
    liker_ids = [str(r[0]) for r in rows_data]
    super_set = {str(r[0]) for r in rows_data if r[1]}

    if not liker_ids:
        return {"profiles": [], "total": 0}

    result = await db.execute(select(User).where(User.id.in_(liker_ids)))
    likers: list[User] = list(result.scalars().all())

    # Calculate distance from current user to each liker
    me_lat = current_user.latitude
    me_lon = current_user.longitude

    profiles = []
    for u in likers:
        dist_km: float | None = None
        if me_lat is not None and me_lon is not None and u.latitude is not None and u.longitude is not None:
            dist_km = _haversine_km(me_lat, me_lon, u.latitude, u.longitude)
        p = _build_profile(u, dist_km)
        p["is_super_like"] = str(u.id) in super_set
        profiles.append(p)

    is_pro = current_user.subscription_tier in ("pro", "premium_plus")
    return {
        "profiles": profiles,
        "total": len(profiles),
        "is_pro": is_pro,
    }


@router.get("/work/daily-status", summary="Return today's work connect usage for the current user")
async def get_work_daily_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Returns how many work connects the user has used today and the daily limit."""
    DAILY_WORK_CONNECT_LIMIT = 20
    now_utc  = datetime.now(timezone.utc)
    wc_reset = current_user.daily_work_connects_reset_at

    # Auto-reset if a new UTC day has begun
    if wc_reset is None or wc_reset.date() < now_utc.date():
        current_user.daily_work_connects_used     = 0
        current_user.daily_work_connects_reset_at = now_utc
        db.add(current_user)
        await db.commit()

    used      = current_user.daily_work_connects_used
    remaining = max(0, DAILY_WORK_CONNECT_LIMIT - used)
    return {
        "connects_used":      used,
        "connects_remaining": remaining,
        "connects_limit":     DAILY_WORK_CONNECT_LIMIT,
        "resets_at":          (now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                               + __import__('datetime').timedelta(days=1)).isoformat(),
    }


@router.delete("/swipes/reset", summary="Clear all swipes for the current user (useful for testing)")
async def reset_swipes(
    mode: str = Query("date", description="Feed mode: 'date' or 'work'"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        text("DELETE FROM swipes WHERE swiper_id = CAST(:uid AS uuid) AND mode = :mode")
        .bindparams(uid=str(current_user.id), mode=mode)
    )
    await db.commit()
    return {"reset": True, "deleted": result.rowcount}


@router.get("/ai-picks", summary="Top 10 profiles sorted by real compatibility score, using current filters")
async def get_ai_picks(
    mode: str = Query("date", description="Feed mode: 'date' or 'work'"),
    halal: bool = Query(False, description="Halal mode"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns the top 10 profiles from the user's filtered discovery pool,
    ranked by their real pairwise compatibility score (highest first).
    Each profile includes:
      - compatibility.percent  – the real match score (0-100)
      - compatibility.brief    – AI-generated match reason
      - compatibility.insights – top matching categories
      - shared_interests       – interests in common with the current user
    """
    await _ensure_cache(db)

    # Over-fetch so we have enough to sort after distance filtering
    pool = await _fetch_discover_profiles(current_user, db, page=0, limit=50, mode=mode, halal=halal)

    # Sort by real compatibility score descending; profiles without a score go last
    pool.sort(key=lambda p: (p.get("compatibility") or {}).get("percent", 0), reverse=True)
    top10 = pool[:10]

    # Compute shared interests (server-side) for each pick
    my_interest_labels: set[str] = {
        item["label"].lower()
        for item in _labels(current_user.interests)
        if item.get("label")
    }

    for p in top10:
        their_interests = p.get("interests") or []
        shared = [
            item for item in their_interests
            if isinstance(item, dict) and item.get("label", "").lower() in my_interest_labels
        ]
        p["shared_interests"] = shared

    return {"profiles": top10, "total": len(top10)}


_DAILY_REVERT_LIMIT = 3
_REVERT_WINDOW_SECONDS = 5 * 60   # 5 minutes


class RevertBody(BaseModel):
    swiped_id: str
    mode: str = "date"


@router.post("/revert", summary="Undo the last left-swipe within the 5-minute window (free: 3/day)")
async def revert_swipe(
    body: RevertBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Removes a left-swipe so the profile can reappear in the user's feed.

    Rules:
    • The swipe must exist, be a *left* swipe, and be ≤ 5 minutes old.
    • Free users may revert at most 3 times per UTC day.
    • Pro / Premium+ users have no daily limit.
    """
    now = datetime.now(timezone.utc)
    is_pro = current_user.subscription_tier in ("pro", "premium_plus")

    # ── Reset daily counter if a new UTC day has started ─────────────────────
    reset_needed = (
        current_user.daily_revert_reset_at is None
        or (now - current_user.daily_revert_reset_at).total_seconds() >= 86400
    )
    if reset_needed:
        current_user.daily_revert_used = 0
        current_user.daily_revert_reset_at = now

    # ── Enforce free-user daily cap ───────────────────────────────────────────
    if not is_pro and current_user.daily_revert_used >= _DAILY_REVERT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Daily revert limit of {_DAILY_REVERT_LIMIT} reached. Resets at midnight UTC.",
        )

    # ── Find the swipe ────────────────────────────────────────────────────────
    row = (
        await db.execute(
            text("""
                SELECT created_at
                FROM swipes
                WHERE swiper_id = CAST(:uid   AS uuid)
                  AND swiped_id = CAST(:swiped AS uuid)
                  AND direction = 'left'
                  AND mode      = :mode
            """).bindparams(
                uid=str(current_user.id),
                swiped=body.swiped_id,
                mode=body.mode,
            )
        )
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No left-swipe found for this profile in the requested mode.",
        )

    swipe_time: datetime = row[0]
    if swipe_time.tzinfo is None:
        swipe_time = swipe_time.replace(tzinfo=timezone.utc)
    elapsed = (now - swipe_time).total_seconds()
    if elapsed > _REVERT_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The 5-minute revert window has expired for this swipe.",
        )

    # ── Delete the swipe ──────────────────────────────────────────────────────
    await db.execute(
        text("""
            DELETE FROM swipes
            WHERE swiper_id = CAST(:uid   AS uuid)
              AND swiped_id = CAST(:swiped AS uuid)
              AND mode      = :mode
        """).bindparams(
            uid=str(current_user.id),
            swiped=body.swiped_id,
            mode=body.mode,
        )
    )

    current_user.daily_revert_used += 1
    db.add(current_user)
    await db.commit()

    used = current_user.daily_revert_used
    remaining = None if is_pro else max(0, _DAILY_REVERT_LIMIT - used)

    return {
        "reverted": True,
        "swiped_id": body.swiped_id,
        "reverts_used": used,
        "reverts_remaining": remaining,
        "reverts_limit": None if is_pro else _DAILY_REVERT_LIMIT,
    }


@router.get("/feed", summary="Get paginated discovery feed based on saved filters")
async def get_discover_feed(
    page: int = Query(0, ge=0, description="Page index (0-based)"),
    limit: int = Query(10, ge=1, le=50, description="Profiles per page"),
    mode: str = Query("date", description="Feed mode: 'date' or 'work'"),
    halal: bool = Query(False, description="Halal mode — filter by same religion + halal-specific preferences"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    FREE_DAILY_LIKE_LIMIT = 20

    # ── Check daily like limit before fetching profiles ───────────────────────
    is_free = current_user.subscription_tier == "free"
    daily_likes_remaining: int | None = None
    daily_limit_reached = False

    if is_free and mode != "work":
        now_utc = datetime.now(timezone.utc)
        dl_reset = current_user.daily_likes_reset_at

        # Auto-reset counter if a new UTC day has begun
        if dl_reset is None or dl_reset.date() < now_utc.date():
            await db.execute(
                text(
                    "UPDATE users SET daily_likes_used = 0, daily_likes_reset_at = :now "
                    "WHERE id = CAST(:uid AS uuid)"
                ).bindparams(now=now_utc, uid=str(current_user.id))
            )
            await db.commit()
            current_user.daily_likes_used = 0
            current_user.daily_likes_reset_at = now_utc

        used = current_user.daily_likes_used or 0
        daily_likes_remaining = max(0, FREE_DAILY_LIKE_LIMIT - used)
        daily_limit_reached = used >= FREE_DAILY_LIKE_LIMIT

    profiles = await _fetch_discover_profiles(current_user, db, page, limit, mode=mode, halal=halal)

    return {
        "page": page,
        "limit": limit,
        "mode": mode,
        "halal": halal,
        "profiles": profiles,
        "has_more": len(profiles) == limit,
        # ── Daily like status (free users, date mode only) ────────────────────
        # When daily_limit_reached=True the client should show the upgrade screen
        # inline in the feed instead of a blank page.
        "daily_limit_reached":  daily_limit_reached,
        "daily_likes_remaining": daily_likes_remaining,
        "daily_likes_limit":    FREE_DAILY_LIKE_LIMIT if is_free else None,
        "is_pro":               not is_free,
    }
