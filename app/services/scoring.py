"""
Compatibility Scoring Engine
============================
Uses OpenAI (gpt-4o-mini) to score a user profile across 8 personality/lifestyle
dimensions (1–10 each). All lookup IDs are resolved to human-readable labels
before being sent to OpenAI so the model understands the actual content.

Falls back to a deterministic heuristic scorer if OpenAI is unavailable.

Categories
----------
1. education   – education background & intellectual curiosity
2. career      – career ambition, work experience & drive
3. lifestyle   – health habits: exercise, diet, drinking, smoking
4. values      – religion, family plans, causes, core values
5. interests   – hobbies, passions, travel, social activities
6. personality – bio authenticity, prompt answers depth & warmth
7. social      – languages, community, communication style
8. intentions  – purpose clarity & relationship-goal specificity

New/empty profiles start at 10 (blank slate = maximum potential).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.models.user_score import UserScore
from app.models.user_compatibility import UserCompatibility

logger = logging.getLogger(__name__)

# ── Category weights (must sum to 1.0) ────────────────────────────────────────
WEIGHTS = {
    "education":   0.12,
    "career":      0.15,
    "lifestyle":   0.13,
    "values":      0.15,
    "interests":   0.13,
    "personality": 0.15,
    "social":      0.10,
    "intentions":  0.07,
}

CATEGORIES = list(WEIGHTS.keys())


# ── Lookup resolution ─────────────────────────────────────────────────────────

async def _load_lookup_map(db: AsyncSession) -> dict[int, str]:
    """Return id→label map for all active lookup options + relationship_types."""
    result: dict[int, str] = {}
    rows = await db.execute(
        text("SELECT id, label FROM lookup_options WHERE is_active = true")
    )
    for row in rows.fetchall():
        result[row[0]] = row[1]
    rrows = await db.execute(
        text("SELECT id, label FROM relationship_types")
    )
    for row in rrows.fetchall():
        result[row[0]] = row[1]
    return result


def _resolve_id(id_: int | None, lmap: dict[int, str]) -> str | None:
    if id_ is None:
        return None
    return lmap.get(id_)


def _resolve_ids(ids: list | None, lmap: dict[int, str]) -> list[str]:
    if not ids:
        return []
    out = []
    for entry in ids:
        id_ = entry["id"] if isinstance(entry, dict) else entry
        label = lmap.get(int(id_))
        if label:
            out.append(label)
    return out


def _resolve_lifestyle(lifestyle: dict | None, lmap: dict[int, str]) -> dict[str, str]:
    if not lifestyle:
        return {}
    return {
        key: lmap.get(int(val), str(val))
        for key, val in lifestyle.items()
        if val is not None
    }


# ── Build rich snapshot ────────────────────────────────────────────────────────

async def _build_rich_snapshot(u: User, db: AsyncSession) -> dict[str, Any]:
    """
    Resolve all lookup IDs to human-readable labels and build a rich
    profile snapshot that OpenAI can actually understand and reason about.
    """
    def age(dob):
        if not dob:
            return None
        today = datetime.now().date()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    lmap = await _load_lookup_map(db)

    # Resolve education history
    education_history = []
    for e in (u.education or []):
        education_history.append({
            "institution": e.get("institution"),
            "course":      e.get("course"),
            "degree":      e.get("degree"),
            "grad_year":   e.get("grad_year"),
        })

    # Resolve work experience
    work_history = []
    for w in (u.work_experience or []):
        work_history.append({
            "job_title": w.get("job_title"),
            "company":   w.get("company"),
            "start_year": w.get("start_year"),
            "end_year":   w.get("end_year"),
            "current":    w.get("current", False),
        })

    # Resolve prompts (Q&A)
    prompts_qa = []
    for p in (u.prompts or []):
        if p.get("answer"):
            prompts_qa.append({
                "question": p.get("question"),
                "answer":   p.get("answer"),
            })

    # Resolve work prompts (Q&A)
    work_prompts_qa = []
    for p in (u.work_prompts or []):
        if p.get("answer"):
            work_prompts_qa.append({
                "question": p.get("question"),
                "answer":   p.get("answer"),
            })

    return {
        # Identity
        "name":            u.full_name,
        "age":             age(u.date_of_birth),
        "bio":             u.bio,
        "mood_status":     u.mood_text,
        "mood_emoji":      u.mood_emoji,

        # Location & background
        "city":            u.city,
        "hometown":        u.hometown,
        "country":         u.country,

        # Personality traits
        "star_sign":       _resolve_id(u.star_sign_id, lmap),

        # Education
        "education_level": _resolve_id(u.education_level_id, lmap),
        "education_history": education_history,

        # Career
        "work_experience": work_history,
        "work_industries": _resolve_ids(u.work_industries, lmap),
        "work_skills":     _resolve_ids(u.work_skills, lmap),
        "work_commitment": _resolve_id(u.work_commitment_level_id, lmap),
        "work_matching_goals": _resolve_ids(u.work_matching_goals, lmap),
        "work_prompts":    work_prompts_qa,
        "is_hiring":       u.work_are_you_hiring,

        # Lifestyle habits (human-readable)
        "lifestyle": _resolve_lifestyle(u.lifestyle, lmap),
        "height_cm": u.height_cm,

        # Values & beliefs
        "religion":        _resolve_id(u.religion_id, lmap),
        "family_plans":    _resolve_id(u.family_plans_id, lmap),
        "want_kids":       _resolve_id(u.have_kids_id, lmap),
        "causes":          _resolve_ids(u.causes, lmap),
        "core_values":     _resolve_ids(u.values_list, lmap),

        # Interests & hobbies
        "interests":       _resolve_ids(u.interests, lmap),

        # Social
        "languages":       _resolve_ids(u.languages, lmap),

        # Personality (from prompts & bio)
        "prompts_answered": prompts_qa,
        "voice_intro":     bool(u.voice_prompts),
        "photos_count":    len(u.photos or []),
        "is_verified":     u.is_verified,

        # Intentions
        "looking_for":     _resolve_id(u.looking_for_id, lmap),
        "relationship_goals": _resolve_ids(u.purpose, lmap),
    }


# ── AI scoring ────────────────────────────────────────────────────────────────

async def _ai_scores(
    snapshot: dict[str, Any],
) -> tuple[dict[str, float], dict[str, str]] | None:
    """
    Send the rich profile snapshot to OpenAI and get scores + reasoning.
    Returns (scores_dict, reasoning_dict) or None on failure.
    """
    if not settings.OPENAI_API_KEY:
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        system = """You are a dating-app personality analyst.
Score the user profile below across exactly 8 categories.
Each score is a float 1.0–10.0.

CRITICAL RULE — judge quality, NOT completeness:
- If a category has NO data at all → score it 6.0 (neutral unknown, do not penalise).
- Only score ABOVE 6 when there is genuine positive signal (rich detail, depth, good habits).
- Only score BELOW 6 when there is clear NEGATIVE signal (e.g. heavy smoking + no exercise, vague one-word bio, contradictory stated goals).
- Never give 1-3 just because a field is empty. Empty = we simply don't know = 6.

Scoring guide (only applies when data IS present):
  6   = neutral / no data — we can't judge
  7   = some data, decent quality
  8   = good depth and specificity
  9   = very rich, authentic, detailed
  10  = exceptional — stands out strongly

Per-category guidance (only when data exists):
- "education": judge the institution prestige, degree relevance, field of study — not just whether it's filled.
- "career": judge job titles, company calibre, career trajectory, entrepreneurial drive, skills depth.
- "lifestyle": judge actual habits — exercise frequency, diet quality, substance use. City/location adds context (Riyadh, Dubai = cosmopolitan = social lifestyle signal).
- "values": judge how clearly articulated and coherent the person's values, causes and beliefs are.
- "interests": judge how diverse, specific and passionate the interests are — generic vs. niche.
- "personality": judge bio authenticity and warmth, prompt answer depth, mood/vibe expressiveness. Hometown vs city adds personal story.
- "social": judge language diversity, community involvement, city context for social reach.
- "intentions": judge how specific and honest they are about what they want in a relationship.

Write a short, human-friendly reasoning (1-2 sentences) per category — mention what stood out or what would help.

Reply ONLY with valid JSON, no markdown:
{"scores":{"education":X,"career":X,"lifestyle":X,"values":X,"interests":X,"personality":X,"social":X,"intentions":X},"reasoning":{"education":"...","career":"...","lifestyle":"...","values":"...","interests":"...","personality":"...","social":"...","intentions":"..."}}"""

        user_msg = (
            "Score this dating profile. All lookup IDs have already been resolved to "
            "human-readable labels so you can understand the content directly.\n\n"
            f"Profile:\n{json.dumps(snapshot, default=str, ensure_ascii=False, indent=2)}"
        )

        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.25,
            max_tokens=700,
            response_format={"type": "json_object"},
        )

        data       = json.loads(resp.choices[0].message.content)
        raw_scores = data.get("scores", {})
        reasoning  = data.get("reasoning", {})

        scores = {
            cat: round(min(10.0, max(1.0, float(raw_scores.get(cat, 7.0)))), 1)
            for cat in CATEGORIES
        }
        return scores, reasoning

    except Exception as exc:
        logger.warning("OpenAI scoring failed, falling back to heuristic: %s", exc)
        return None


# ── Heuristic fallback ────────────────────────────────────────────────────────

def _heuristic_scores(u: User) -> dict[str, float]:
    """
    Fallback when OpenAI is unavailable.
    Empty fields default to 6.0 (neutral) — we only go above/below when
    there is genuine signal to judge, mirroring the AI prompt's logic.
    """
    def has(v) -> bool:
        return v is not None and v != [] and v != {}

    # Education — start neutral, reward depth of what's there
    education = 6.0
    if has(u.education_level_id): education += 1.0
    if has(u.education):          education += min(3.0, len(u.education) * 1.0)

    # Career — start neutral, reward richness
    career = 6.0
    if has(u.work_experience):    career += min(2.5, len(u.work_experience) * 1.0)
    if has(u.work_skills):        career += min(1.0, len(u.work_skills or []) * 0.2)
    if has(u.work_industries):    career += 0.5

    # Lifestyle — start neutral, reward healthy habits
    lifestyle = 6.0
    if has(u.lifestyle):
        filled = sum(1 for v in u.lifestyle.values() if v)
        lifestyle += (filled / 4) * 4.0

    # Values — start neutral, reward articulation
    values = 6.0
    if has(u.religion_id):     values += 0.5
    if has(u.family_plans_id): values += 0.5
    if has(u.have_kids_id):    values += 0.5
    if has(u.values_list):     values += min(2.5, len(u.values_list) * 0.5)
    if has(u.causes):          values += min(1.0, len(u.causes) * 0.3)

    # Interests — start neutral, reward diversity
    interests = 6.0
    if has(u.interests):       interests += min(4.0, len(u.interests) * 0.5)

    # Personality — start neutral, reward authentic expression
    personality = 6.0
    if has(u.bio) and len(u.bio or "") > 50:  personality += 1.5
    if has(u.bio) and len(u.bio or "") > 150: personality += 1.0
    if has(u.prompts):
        answered = [p for p in (u.prompts or []) if p.get("answer")]
        personality += min(2.5, len(answered) * 0.8)
    if has(u.mood_text): personality += 0.5

    # Social — start neutral
    social = 6.0
    if has(u.languages): social += min(2.5, len(u.languages) * 0.8)
    if has(u.causes):    social += min(1.5, len(u.causes) * 0.4)

    # Intentions — start neutral, reward clarity
    intentions = 6.0
    if has(u.purpose):        intentions += min(2.0, len(u.purpose) * 0.7)
    if has(u.looking_for_id): intentions += 2.0

    return {
        cat: round(min(10.0, max(1.0, v)), 1)
        for cat, v in {
            "education":   education,
            "career":      career,
            "lifestyle":   lifestyle,
            "values":      values,
            "interests":   interests,
            "personality": personality,
            "social":      social,
            "intentions":  intentions,
        }.items()
    }


# ── Weighted overall ──────────────────────────────────────────────────────────

def _weighted_overall(scores: dict[str, float]) -> float:
    total = sum(scores[c] * WEIGHTS[c] for c in CATEGORIES)
    return round(min(10.0, max(1.0, total)), 2)


# ── Profile change detection ──────────────────────────────────────────────────

# Fields that affect any scoring dimension. Changes to these trigger a rescore.
_SCORED_FIELDS = (
    "bio", "date_of_birth", "gender_id", "education_level_id", "education",
    "work_experience", "work_industries", "work_skills", "work_commitment_level_id",
    "work_matching_goals", "lifestyle", "height_cm",
    "religion_id", "family_plans_id", "have_kids_id", "values_list", "causes",
    "interests", "languages", "prompts", "mood_text",
    "looking_for_id", "purpose", "star_sign_id",
)


def _profile_hash(user: User) -> str:
    """Return an MD5 hex digest of all profile fields that affect scoring."""
    payload = {f: getattr(user, f, None) for f in _SCORED_FIELDS}
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

async def compute_and_save_score(user: User, db: AsyncSession) -> UserScore:
    """
    Compute score via OpenAI (falls back to heuristic) and persist.
    If the profile hash matches the stored one, the existing score is returned
    immediately — no OpenAI call is made, no DB write occurs.

    Uses an atomic INSERT ... ON CONFLICT (user_id) DO UPDATE to avoid the
    SELECT→INSERT race condition that caused deadlocks under concurrent load.
    """
    current_hash = _profile_hash(user)
    existing = await db.scalar(select(UserScore).where(UserScore.user_id == user.id))

    # Cache hit: profile hasn't changed since last score — return as-is
    if existing and existing.overall is not None and existing.profile_hash == current_hash:
        logger.debug("Score cache hit for user %s (hash %s)", user.id, current_hash)
        return existing

    # Cache miss: build snapshot while we still have the session open, then
    # commit to release all read locks (on user_scores, lookup_options, etc.)
    # BEFORE the slow OpenAI call.  Holding an open transaction across a
    # multi-second network call was the root cause of cross-request deadlocks.
    snapshot = await _build_rich_snapshot(user, db)
    existing_version = (existing.version or 1) + 1 if existing else 1
    await db.commit()  # release read locks; upsert below starts a fresh tx

    result = await _ai_scores(snapshot)
    if result:
        scores, reasoning = result
    else:
        scores    = _heuristic_scores(user)
        reasoning = {c: "Computed via profile completeness analysis." for c in CATEGORIES}

    overall  = _weighted_overall(scores)
    now      = datetime.now(timezone.utc)
    new_version = existing_version

    insert_values = {
        "user_id":      user.id,
        "overall":      overall,
        "reasoning":    reasoning,
        "profile_hash": current_hash,
        "version":      new_version,
        "scored_at":    now,
        **{cat: scores[cat] for cat in CATEGORIES},
    }

    # Atomic upsert — serialised by the unique index on user_id, so concurrent
    # requests for the same user can never deadlock via SELECT→INSERT races.
    stmt = (
        pg_insert(UserScore)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "overall":      overall,
                "reasoning":    reasoning,
                "profile_hash": current_hash,
                "version":      new_version,
                "scored_at":    now,
                **{cat: scores[cat] for cat in CATEGORIES},
            },
        )
    )
    await db.execute(stmt)
    await db.commit()

    # Re-fetch the row so callers receive a fully-hydrated ORM object
    row = await db.scalar(select(UserScore).where(UserScore.user_id == user.id))
    logger.info("Rescored user %s → overall=%.2f (hash %s)", user.id, overall, current_hash)
    return row  # type: ignore[return-value]


def heuristic_score_obj(user: User) -> "SimpleNamespaceScore":
    """
    Return an in-memory score object computed purely from heuristics (no DB, no OpenAI).
    Compatible with compatibility_between() since it uses getattr() duck-typing.
    Use this as a fallback when no UserScore row exists for a candidate.
    """
    scores = _heuristic_scores(user)

    class SimpleNamespaceScore:
        pass

    obj = SimpleNamespaceScore()
    for cat, val in scores.items():
        setattr(obj, cat, val)
    return obj  # type: ignore[return-value]


async def get_or_create_score(user: User, db: AsyncSession) -> UserScore:
    """
    Return the cached score if the profile is unchanged, otherwise compute fresh.
    This is safe to call on every profile view — the hash check avoids unnecessary
    OpenAI calls when nothing has changed.

    Delegates entirely to compute_and_save_score which performs an atomic upsert,
    so there is no separate SELECT here that could race with the write.
    """
    return await compute_and_save_score(user, db)


_CATEGORY_META: dict[str, dict[str, str]] = {
    "education":   {"emoji": "🎓", "label": "Education"},
    "career":      {"emoji": "💼", "label": "Career"},
    "lifestyle":   {"emoji": "🏃", "label": "Lifestyle"},
    "values":      {"emoji": "🌟", "label": "Values"},
    "interests":   {"emoji": "❤️", "label": "Interests"},
    "personality": {"emoji": "✨", "label": "Personality"},
    "social":      {"emoji": "🌍", "label": "Social"},
    "intentions":  {"emoji": "🎯", "label": "Intentions"},
}


def compatibility_between(score_a: UserScore, score_b: UserScore) -> dict[str, Any]:
    """
    Pairwise compatibility between two users.
    Returns percent (0-100), tier label, per-category similarity breakdown,
    top insight chips, and a short human-readable brief.
    """
    breakdown: dict[str, float] = {}
    for cat in CATEGORIES:
        a = getattr(score_a, cat) or 7.0
        b = getattr(score_b, cat) or 7.0
        sim = max(0.0, 1.0 - abs(a - b) / 9.0)
        breakdown[cat] = round(sim * 100, 1)

    total   = sum(breakdown[c] * WEIGHTS[c] for c in CATEGORIES)
    percent = round(min(100.0, max(0.0, total / sum(WEIGHTS.values()))), 1)

    # Top 4 strongest matching categories → insight chips for the frontend
    sorted_cats = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
    insights = [
        {"emoji": _CATEGORY_META[cat]["emoji"], "label": _CATEGORY_META[cat]["label"]}
        for cat, score in sorted_cats[:4]
        if score >= 60
    ]

    # Short human-readable brief driven by percent tier
    tier = _tier(percent)
    if tier == "soulmate":
        brief = "Exceptionally aligned across values, lifestyle, and intentions."
    elif tier == "great_match":
        top = [_CATEGORY_META[c]["label"] for c, _ in sorted_cats[:2]]
        brief = f"Strong chemistry — especially in {' & '.join(top).lower()}."
    elif tier == "good_match":
        top = [_CATEGORY_META[c]["label"] for c, _ in sorted_cats[:1]]
        brief = f"Good foundation with shared {top[0].lower() if top else 'interests'}."
    elif tier == "moderate":
        brief = "Some common ground — differences could spark interesting conversations."
    else:
        brief = "Different paths, but opposites sometimes attract."

    return {
        "percent":   percent,
        "breakdown": breakdown,
        "tier":      tier,
        "insights":  insights,
        "brief":     brief,
    }


def _tier(pct: float) -> str:
    if pct >= 85: return "soulmate"
    if pct >= 70: return "great_match"
    if pct >= 55: return "good_match"
    if pct >= 40: return "moderate"
    return "low"


def _pair_hash(hash_a: str | None, hash_b: str | None) -> str:
    """Stable hash of two users' profile hashes — order-independent."""
    combined = "".join(sorted([hash_a or "", hash_b or ""]))
    return hashlib.md5(combined.encode()).hexdigest()


async def get_or_compute_compatibility(
    user_a: User,
    user_b: User,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Return the cached pairwise compatibility dict or compute + persist it.

    The result is stored in `user_compatibility` with a stable canonical key
    (smaller UUID first). A pair is only recomputed when either user's profile
    has changed (detected via their individual `profile_hash` values).
    """
    import uuid as _uuid

    # Stable ordering so we always have one row per pair
    uid_a, uid_b = sorted([user_a.id, user_b.id], key=lambda u: str(u))

    existing: UserCompatibility | None = await db.scalar(
        select(UserCompatibility).where(
            UserCompatibility.user_a_id == uid_a,
            UserCompatibility.user_b_id == uid_b,
        )
    )

    # Fetch individual scores (uses per-user hash cache)
    score_a = await get_or_create_score(user_a, db)
    score_b = await get_or_create_score(user_b, db)

    current_pair_hash = _pair_hash(score_a.profile_hash, score_b.profile_hash)

    # Cache hit: both individual scores unchanged → return stored result
    if existing and existing.score_hash == current_pair_hash:
        logger.debug("Compat cache hit for pair %s ↔ %s", uid_a, uid_b)
        return {
            "percent":   existing.percent,
            "tier":      existing.tier,
            "breakdown": existing.breakdown,
            "insights":  existing.insights,
            "brief":     existing.brief,
        }

    # Cache miss: compute fresh
    result = compatibility_between(score_a, score_b)
    now    = datetime.now(timezone.utc)

    # Atomic upsert — prevents SELECT→INSERT race on the (user_a_id, user_b_id) pair
    compat_stmt = (
        pg_insert(UserCompatibility)
        .values(
            user_a_id   = uid_a,
            user_b_id   = uid_b,
            percent     = result["percent"],
            tier        = result["tier"],
            breakdown   = result["breakdown"],
            insights    = result["insights"],
            brief       = result["brief"],
            score_hash  = current_pair_hash,
            computed_at = now,
        )
        .on_conflict_do_update(
            constraint="uq_user_compat_pair",
            set_={
                "percent":     result["percent"],
                "tier":        result["tier"],
                "breakdown":   result["breakdown"],
                "insights":    result["insights"],
                "brief":       result["brief"],
                "score_hash":  current_pair_hash,
                "computed_at": now,
            },
        )
    )
    await db.execute(compat_stmt)
    await db.commit()
    logger.info("Compat saved for pair %s ↔ %s → %.1f%%", uid_a, uid_b, result["percent"])
    return result
