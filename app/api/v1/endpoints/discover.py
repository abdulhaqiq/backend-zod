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
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_score import UserScore
from app.services.scoring import compatibility_between, get_or_create_score

# Lazy import to avoid circular — resolved at call time
def _get_notify_manager():
    from app.api.v1.endpoints.chat import notify_manager
    return notify_manager

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
        "age":        age,
        "verified":   u.is_verified,
        "premium":    u.subscription_tier == "pro",
        "location":   u.city,
        "distance":   dist_str,
        "distance_km": distance_km,
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
        "mood": {"emoji": u.mood_emoji, "text": u.mood_text} if u.mood_text else None,
        "compatibility": compat,  # {percent, tier, breakdown} or None
        # Work profile fields (populated for work mode)
        "work": {
            "matchingGoals":   [x["label"] for x in _labels(u.work_matching_goals)],
            "commitmentLevel": _label(u.work_commitment_level_id),
            "equitySplit":     _label(u.work_equity_split_id),
            "industries":      [x["label"] for x in _labels(u.work_industries)],
            "skills":          [x["label"] for x in _labels(u.work_skills)],
            "areYouHiring":    u.work_are_you_hiring,
            "schedulingUrl":   u.work_scheduling_url,
            "prompts":         list(u.work_prompts or []),
            "photos":          list(u.work_photos or []),
        } if (u.work_matching_goals or u.work_commitment_level_id) else None,
    }


# ── Query builder ─────────────────────────────────────────────────────────────

async def _fetch_discover_profiles(
    me: User,
    db: AsyncSession,
    page: int,
    limit: int,
    mode: str = "date",
) -> list[dict]:
    await _ensure_cache(db)

    is_pro = me.subscription_tier == "pro"

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
    if opposite_gender_id is not None:
        base_filters.append(User.gender_id == opposite_gender_id)

    stmt = (
        select(User)
        .where(*base_filters)
        .order_by(User.created_at.desc())
        .offset(page * limit)
        .limit(limit * 5)   # over-fetch so chip-away post-filters leave enough candidates
    )

    # ── Exclude already-swiped profiles ───────────────────────────────────────
    swiped_result = await db.execute(
        text("SELECT swiped_id FROM swipes WHERE swiper_id = CAST(:uid AS uuid) AND mode = :mode")
        .bindparams(uid=str(me.id), mode=mode)
    )
    swiped_ids = [row[0] for row in swiped_result.fetchall()]
    if swiped_ids:
        stmt = stmt.where(User.id.not_in(swiped_ids))

    # ── Distance filter pushed into SQL (Haversine bounding-box + exact check) ─
    # When we have the current user's location and a max_km cap, filter in SQL so
    # we don't fetch thousands of far-away rows only to drop them in Python.
    if me.latitude is not None and me.longitude is not None and me.filter_max_distance_km:
        _max_km = float(me.filter_max_distance_km)
        _lat    = float(me.latitude)
        _lon    = float(me.longitude)
        stmt = stmt.where(
            text(
                # Haversine entirely in SQL — no PostGIS required.
                "latitude  IS NOT NULL AND longitude IS NOT NULL AND "
                "2 * 6371 * ASIN(SQRT("
                "  POWER(SIN(RADIANS(latitude  - :lat) / 2), 2) + "
                "  COS(RADIANS(:lat)) * COS(RADIANS(latitude)) * "
                "  POWER(SIN(RADIANS(longitude - :lon) / 2), 2)"
                ")) <= :max_km"
            ).bindparams(lat=_lat, lon=_lon, max_km=_max_km)
        )

    # Work mode: only show users who have a work profile set up
    if mode == "work":
        stmt = stmt.where(
            User.work_matching_goals.isnot(None) | User.work_commitment_level_id.isnot(None)
        )

    # ── Verified-only filter ──────────────────────────────────────────────────
    if me.filter_verified_only:
        stmt = stmt.where(User.is_verified.is_(True))

    # ── Age filter ────────────────────────────────────────────────────────────
    if me.filter_age_min or me.filter_age_max:
        today = date.today()
        if me.filter_age_max:
            max_age = me.filter_age_max
            min_dob = date(today.year - max_age - 1, today.month, today.day)
            stmt = stmt.where(User.date_of_birth >= min_dob)
        if me.filter_age_min:
            min_age = me.filter_age_min
            max_dob = date(today.year - min_age, today.month, today.day)
            stmt = stmt.where(User.date_of_birth <= max_dob)

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

    result = await db.execute(stmt)
    candidates: list[User] = list(result.scalars().all())

    # ── Distance calculation (Python-side, for display only) ─────────────────
    # SQL already excluded out-of-range profiles when coordinates are available.
    # Here we only compute the display string and handle any edge cases.
    me_lat = me.latitude
    me_lon = me.longitude
    max_km = me.filter_max_distance_km

    has_my_location = me_lat is not None and me_lon is not None

    # ── Compatibility scores ──────────────────────────────────────────────────
    my_score = await get_or_create_score(me, db)

    # Bulk-fetch scores for all candidates in one query
    candidate_ids = [u.id for u in candidates]
    score_rows = {}
    if candidate_ids:
        score_result = await db.execute(
            select(UserScore).where(UserScore.user_id.in_(candidate_ids))
        )
        for sr in score_result.scalars().all():
            score_rows[sr.user_id] = sr

    profiles = []
    for u in candidates:
        dist_km: float | None = None
        if has_my_location and u.latitude is not None and u.longitude is not None:
            dist_km = _haversine_km(me_lat, me_lon, u.latitude, u.longitude)
            if max_km is not None and dist_km > max_km:
                continue
        elif max_km is not None and has_my_location and (u.latitude is None or u.longitude is None):
            continue

        their_score = score_rows.get(u.id)
        compat = compatibility_between(my_score, their_score) if their_score else None

        profiles.append(_build_profile(u, dist_km, compat))
        if len(profiles) >= limit:
            break

    return profiles


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
            # Push match event to both users
            match_payload = {"type": "match", "profile": liker_profile}
            await nm.send_to(body.swiped_id, match_payload)
            swiped_result = await db.execute(select(User).where(User.id == body.swiped_id))
            swiped_user = swiped_result.scalar_one_or_none()
            if swiped_user:
                await nm.send_to(str(current_user.id), {"type": "match", "profile": _build_profile(swiped_user, None)})
        else:
            # Push liked_you (or super_like) event to the person being liked
            event_type = "super_like" if is_super else "liked_you"
            payload: dict[str, Any] = {"type": event_type, "profile": liker_profile}
            if is_super and body.ice_breaker:
                payload["ice_breaker"] = body.ice_breaker
            await nm.send_to(body.swiped_id, payload)

    return {"recorded": True, "match": is_match, "super": is_super}


@router.get("/counts", summary="Get badge counts for the current user")
async def get_counts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Returns liked_you, matches, views (swipes received) counts."""
    uid = str(current_user.id)

    liked_row = await db.execute(
        text("SELECT COUNT(*) FROM likes WHERE liked_id = CAST(:uid AS uuid)").bindparams(uid=uid)
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

    is_pro = current_user.subscription_tier == "pro"
    return {
        "profiles": profiles,
        "total": len(profiles),
        "is_pro": is_pro,
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


@router.get("/feed", summary="Get paginated discovery feed based on saved filters")
async def get_discover_feed(
    page: int = Query(0, ge=0, description="Page index (0-based)"),
    limit: int = Query(10, ge=1, le=50, description="Profiles per page"),
    mode: str = Query("date", description="Feed mode: 'date' or 'work'"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    profiles = await _fetch_discover_profiles(current_user, db, page, limit, mode=mode)
    return {
        "page": page,
        "limit": limit,
        "mode": mode,
        "profiles": profiles,
        "has_more": len(profiles) == limit,
    }
