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

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.models.user_score import UserScore

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

    return {
        # Identity
        "name":            u.full_name,
        "age":             age(u.date_of_birth),
        "bio":             u.bio,
        "mood_status":     u.mood_text,

        # Education
        "education_level": _resolve_id(u.education_level_id, lmap),
        "education_history": education_history,

        # Career
        "work_experience": work_history,

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
Each score is a float 1.0–10.0. Use the FULL 1–10 range:
  1-3 = very low / almost no data
  4-6 = moderate / partially filled
  7-8 = good / well developed
  9-10 = exceptional / very rich

Important rules:
- A brand-new profile with almost nothing filled in scores 10 (maximum potential — clean slate).
- Score based on depth, quality and richness of what the person has shared, NOT just quantity.
- For "interests": score how diverse and rich the person's hobbies/passions are.
- For "lifestyle": score how healthy and consistent their habits are (exercise, diet, no smoking/drinking = higher).
- For "personality": score how authentic, warm and thoughtful their bio and prompt answers are.
- For "intentions": score how clear and specific they are about what they want.
- Write a short, human-friendly reasoning (1-2 sentences) per category.

Reply ONLY with valid JSON, no markdown. Format exactly:
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
    """Fallback when OpenAI is unavailable — profile completeness heuristic."""
    def has(v) -> bool:
        return v is not None and v != [] and v != {}

    def list_score(lst, full: int = 5) -> float:
        if not lst:
            return 1.0
        return min(10.0, 1.0 + (len(lst) / full) * 9.0)

    education = 5.0
    if has(u.education_level_id): education += 2.0
    if has(u.education):          education += min(3.0, len(u.education) * 1.5)

    career = 5.0
    if has(u.work_experience):    career += min(5.0, len(u.work_experience) * 1.5)

    lifestyle = 5.0
    if has(u.lifestyle):
        filled = sum(1 for v in u.lifestyle.values() if v)
        lifestyle += (filled / 4) * 5.0

    values = 5.0
    if has(u.religion_id):     values += 1.5
    if has(u.family_plans_id): values += 1.5
    if has(u.values_list):     values += min(2.0, len(u.values_list) * 0.5)

    interests = list_score(u.interests, 6)

    personality = 3.0
    if has(u.bio) and len(u.bio or "") > 30:  personality += 3.0
    if has(u.prompts):
        answered = [p for p in (u.prompts or []) if p.get("answer")]
        personality += min(4.0, len(answered) * 1.3)

    social = 5.0
    if has(u.languages): social += min(3.0, len(u.languages) * 1.0)
    if has(u.causes):    social += 2.0

    intentions = 5.0
    if has(u.purpose):        intentions += 2.5
    if has(u.looking_for_id): intentions += 2.5

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


# ── Public API ────────────────────────────────────────────────────────────────

async def compute_and_save_score(user: User, db: AsyncSession) -> UserScore:
    """
    Compute score using OpenAI (falls back to heuristic), persist and return.
    All lookup IDs are resolved to labels before sending to AI.
    """
    snapshot = await _build_rich_snapshot(user, db)

    result = await _ai_scores(snapshot)
    if result:
        scores, reasoning = result
    else:
        scores    = _heuristic_scores(user)
        reasoning = {c: "Computed via profile completeness analysis." for c in CATEGORIES}

    overall = _weighted_overall(scores)

    existing = await db.scalar(select(UserScore).where(UserScore.user_id == user.id))
    if existing:
        for cat in CATEGORIES:
            setattr(existing, cat, scores[cat])
        existing.overall   = overall
        existing.reasoning = reasoning
        existing.version   = (existing.version or 1) + 1
        existing.scored_at = datetime.now(timezone.utc)
        row = existing
    else:
        row = UserScore(
            user_id   = user.id,
            overall   = overall,
            reasoning = reasoning,
            version   = 1,
            scored_at = datetime.now(timezone.utc),
            **{cat: scores[cat] for cat in CATEGORIES},
        )
        db.add(row)

    await db.commit()
    await db.refresh(row)
    return row


async def get_or_create_score(user: User, db: AsyncSession) -> UserScore:
    """Return existing score or compute fresh one if missing."""
    existing = await db.scalar(select(UserScore).where(UserScore.user_id == user.id))
    if existing and existing.overall is not None:
        return existing
    return await compute_and_save_score(user, db)


def compatibility_between(score_a: UserScore, score_b: UserScore) -> dict[str, Any]:
    """
    Pairwise compatibility between two users.
    Returns percent (0-100), tier label, and per-category similarity breakdown.
    """
    breakdown: dict[str, float] = {}
    for cat in CATEGORIES:
        a = getattr(score_a, cat) or 7.0
        b = getattr(score_b, cat) or 7.0
        sim = max(0.0, 1.0 - abs(a - b) / 9.0)
        breakdown[cat] = round(sim * 100, 1)

    total   = sum(breakdown[c] * WEIGHTS[c] for c in CATEGORIES)
    percent = round(min(100.0, max(0.0, total / sum(WEIGHTS.values()))), 1)

    return {
        "percent":   percent,
        "breakdown": breakdown,
        "tier":      _tier(percent),
    }


def _tier(pct: float) -> str:
    if pct >= 85: return "soulmate"
    if pct >= 70: return "great_match"
    if pct >= 55: return "good_match"
    if pct >= 40: return "moderate"
    return "low"
