"""
Cards API — fetch question and truth-or-dare cards from database.

GET /api/v1/cards                         — list all games (question | truth_or_dare)
GET /api/v1/cards/{game}                  — list categories for a game
GET /api/v1/cards/{game}/{category}       — list cards for a game+category (ordered)
POST /api/v1/cards/generate               — AI-generate a truth/dare card from chat context
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.card import Card

_log = logging.getLogger(__name__)

cards_router = APIRouter(prefix="/cards", tags=["cards"])


class CardOut(BaseModel):
    id: str
    game: str
    category: str
    tag: str
    emoji: str
    question: str
    color: str
    sort_order: int

    model_config = {"from_attributes": True}


class GameOut(BaseModel):
    game: str
    categories: list[str]


# ── AI card generation ────────────────────────────────────────────────────────

class AiCardRequest(BaseModel):
    choice: str          # "truth" | "dare"
    category: str        # "Spicy" | "Romantic" | "Fun" | "Deep"
    chat_context: list[str] = []   # last N message texts for context


class AiCardOut(BaseModel):
    question: str
    emoji: str
    color: str


_CATEGORY_COLORS = {
    "Spicy":    "#7f1d1d",
    "Romantic": "#831843",
    "Fun":      "#78350f",
    "Deep":     "#1e1b4b",
    "Truth":    "#312e81",
    "Dare":     "#7f1d1d",
}

_CATEGORY_EMOJIS = {
    "Spicy": "🌶️", "Romantic": "💕", "Fun": "😂", "Deep": "🌊",
    "Truth": "🤔", "Dare": "🔥",
}


@cards_router.post("/generate", response_model=AiCardOut)
async def generate_ai_card(
    body: AiCardRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Uses OpenAI to generate a personalised Truth or Dare card.
    Falls back to a generic card if the API key is missing or the call fails.
    """
    choice   = body.choice.lower()
    category = body.category
    context  = body.chat_context[-12:] if body.chat_context else []

    # Fallback defaults
    fallback_truths = {
        "Spicy":    "What's the most daring thing you've ever done for someone you liked?",
        "Romantic": "What song would you dedicate to me right now?",
        "Fun":      "What's the weirdest thing you've Googled in the last week?",
        "Deep":     "What's one thing you've never told anyone about yourself?",
    }
    fallback_dares = {
        "Spicy":    "Send me a photo of your best selfie right now, no filters!",
        "Romantic": "Write me a 2-line poem about us — you have 60 seconds.",
        "Fun":      "Do your best impression of me and describe it in a voice note.",
        "Deep":     "Share one thing you've been putting off that you wish you could change.",
    }

    if not settings.OPENAI_API_KEY:
        fb = fallback_truths if choice == "truth" else fallback_dares
        question = fb.get(category, fb.get("Fun", "Tell me something surprising about you!"))
        return AiCardOut(
            question=question,
            emoji=_CATEGORY_EMOJIS.get(category, "🎲"),
            color=_CATEGORY_COLORS.get(category, "#1e1b4b"),
        )

    try:
        import httpx

        context_block = ""
        if context:
            context_block = "\n\nRecent chat messages between the two users:\n" + "\n".join(f"- {m}" for m in context)

        system_prompt = (
            "You are a creative game-card writer for a dating app. "
            "Write a single, engaging Truth or Dare card tailored to the given category and context. "
            "Keep it tasteful but fun and slightly personal. "
            "Return JSON only: {\"question\": \"...\", \"emoji\": \"...\"}. "
            "No markdown, no explanation, only the JSON object."
        )
        user_prompt = (
            f"Generate a {choice.upper()} card. Category: {category}.{context_block}\n\n"
            "Make it feel personal and relevant to what they've been talking about."
        )

        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "max_tokens": 120,
                    "temperature": 0.85,
                },
            )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        return AiCardOut(
            question=parsed["question"],
            emoji=parsed.get("emoji", _CATEGORY_EMOJIS.get(category, "🎲")),
            color=_CATEGORY_COLORS.get(category, "#1e1b4b"),
        )

    except Exception as exc:
        _log.warning("AI card generation failed: %s", exc)
        fb = fallback_truths if choice == "truth" else fallback_dares
        question = fb.get(category, "Tell me something surprising about you!")
        return AiCardOut(
            question=question,
            emoji=_CATEGORY_EMOJIS.get(category, "🎲"),
            color=_CATEGORY_COLORS.get(category, "#1e1b4b"),
        )


@cards_router.get("", response_model=list[GameOut])
async def list_games(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all active games and their categories."""
    rows = await db.execute(
        select(Card.game, Card.category)
        .where(Card.is_active == True)
        .distinct()
        .order_by(Card.game, Card.category)
    )
    result: dict[str, list[str]] = {}
    for game, cat in rows.all():
        result.setdefault(game, []).append(cat)
    return [{"game": g, "categories": cats} for g, cats in result.items()]


@cards_router.get("/{game}", response_model=list[str])
async def list_categories(
    game: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(Card.category)
        .where(Card.game == game, Card.is_active == True)
        .distinct()
        .order_by(Card.category)
    )
    return [r[0] for r in rows.all()]


@cards_router.get("/{game}/{category}", response_model=list[CardOut])
async def list_cards(
    game: str,
    category: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(Card)
        .where(Card.game == game, Card.category == category, Card.is_active == True)
        .order_by(Card.sort_order)
    )
    cards = rows.scalars().all()
    return [
        CardOut(
            id=str(c.id),
            game=c.game,
            category=c.category,
            tag=c.tag,
            emoji=c.emoji,
            question=c.question,
            color=c.color,
            sort_order=c.sort_order,
        )
        for c in cards
    ]
