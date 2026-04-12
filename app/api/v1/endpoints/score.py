"""
Score endpoints
===============
GET  /score/me                           — return my current score (compute if missing)
POST /score/me/refresh                   — force-recompute my score using latest profile data
GET  /score/vs/{other_user_id}           — get compatibility % between me and another user (saves to DB)
GET  /score/vs/{other_user_id}/cached    — return saved compatibility without charging credits (null if none)
"""
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_compatibility import UserCompatibility
from app.models.user_score import UserScore
from app.services.scoring import (
    compute_and_save_score,
    get_or_create_score,
    get_or_compute_compatibility,
    CATEGORIES,
)

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/score", tags=["score"])


# ── Response schemas ──────────────────────────────────────────────────────────

class CategoryScore(BaseModel):
    category:  str
    score:     float        # 1.0 – 10.0
    reasoning: str | None = None


class MyScoreResponse(BaseModel):
    overall:    float                  # weighted average 1-10
    categories: list[CategoryScore]
    version:    int
    scored_at:  str | None

    class Config:
        from_attributes = True


class CompatibilityResponse(BaseModel):
    percent:       float               # 0-100
    tier:          str                 # soulmate | great_match | good_match | moderate | low
    breakdown:     dict[str, float]    # {category: similarity_percent}
    my_overall:    float
    their_overall: float


def _build_my_score_response(row: UserScore) -> MyScoreResponse:
    reasoning = row.reasoning or {}
    cats = [
        CategoryScore(
            category  = cat,
            score     = getattr(row, cat) or 7.0,
            reasoning = reasoning.get(cat),
        )
        for cat in CATEGORIES
    ]
    return MyScoreResponse(
        overall    = row.overall or 7.0,
        categories = cats,
        version    = row.version or 1,
        scored_at  = row.scored_at.isoformat() if row.scored_at else None,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=MyScoreResponse)
async def get_my_score(
    current_user: User       = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """Return current user's personality score. Computes on first call."""
    row = await get_or_create_score(current_user, db)
    return _build_my_score_response(row)


@router.post("/me/refresh", response_model=MyScoreResponse)
async def refresh_my_score(
    current_user: User       = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """
    Force-recompute the score using the latest profile data and OpenAI.
    Useful after major profile updates.
    """
    row = await compute_and_save_score(current_user, db)
    return _build_my_score_response(row)


@router.get("/vs/{other_user_id}", response_model=CompatibilityResponse)
async def get_compatibility(
    other_user_id: uuid.UUID,
    current_user:  User       = Depends(get_current_user),
    db:            AsyncSession = Depends(get_db),
):
    """
    Compute pairwise compatibility between the current user and another.
    Persists the result in user_compatibility table for future free retrieval.
    """
    other = await db.scalar(
        select(User).where(User.id == other_user_id, User.is_active == True)
    )
    if not other:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    my_score    = await get_or_create_score(current_user, db)
    their_score = await get_or_create_score(other, db)

    result = await get_or_compute_compatibility(current_user, other, db)
    return CompatibilityResponse(
        percent       = result["percent"],
        tier          = result["tier"],
        breakdown     = result["breakdown"],
        my_overall    = my_score.overall or 7.0,
        their_overall = their_score.overall or 7.0,
    )


@router.get("/vs/{other_user_id}/cached", response_model=CompatibilityResponse | None)
async def get_compatibility_cached(
    other_user_id: uuid.UUID,
    current_user:  User       = Depends(get_current_user),
    db:            AsyncSession = Depends(get_db),
):
    """
    Return the stored compatibility score without computing or charging credits.
    Returns null (204) if no compatibility has been computed yet for this pair.
    """
    uid_a, uid_b = sorted([current_user.id, other_user_id], key=lambda u: str(u))
    existing: UserCompatibility | None = await db.scalar(
        select(UserCompatibility).where(
            UserCompatibility.user_a_id == uid_a,
            UserCompatibility.user_b_id == uid_b,
        )
    )
    if not existing:
        return None

    my_score    = await get_or_create_score(current_user, db)
    other       = await db.scalar(select(User).where(User.id == other_user_id))
    their_score = await get_or_create_score(other, db) if other else None

    return CompatibilityResponse(
        percent       = existing.percent,
        tier          = existing.tier,
        breakdown     = existing.breakdown or {},
        my_overall    = my_score.overall or 7.0,
        their_overall = (their_score.overall if their_score else 7.0) or 7.0,
    )
