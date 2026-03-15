"""
Mini-Games API

GET  /api/v1/mini-games                           — list all active games from DB
GET  /api/v1/mini-games/{game_type}/cards         — get cards for a game type (with optional ?category=)
GET  /api/v1/mini-games/{game_type}/cards/random  — get one random card for a game type
POST /api/v1/mini-games/response                  — save a player's response to a game move
GET  /api/v1/mini-games/responses/{room_id}       — get all responses for a chat room
GET  /api/v1/mini-games/response/{game_message_id} — get responses for a specific game message
"""
from __future__ import annotations

import json
import logging
import random
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

_log = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.card import Card
from app.models.mini_game import GameResponse, MiniGame

mini_games_router = APIRouter(prefix="/mini-games", tags=["mini-games"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class MiniGameOut(BaseModel):
    id: str
    game_type: str
    name: str
    tagline: str
    description: str
    emoji: str
    accent_color: str
    bg_color: str
    categories: list[str]
    sort_order: int

    model_config = {"from_attributes": True}


class GameCardOut(BaseModel):
    id: str
    game: str
    category: str
    tag: str
    emoji: str
    question: str
    color: str
    sort_order: int
    # Parsed options for structured games
    option_a: str | None = None
    option_b: str | None = None
    options: list[str] | None = None

    model_config = {"from_attributes": True}


class GameResponseIn(BaseModel):
    game_message_id: str
    game_type: str
    room_id: str
    response_data: dict[str, Any]


class GameResponseOut(BaseModel):
    id: str
    game_message_id: str
    game_type: str
    room_id: str
    responder_id: str
    response_data: dict[str, Any]
    created_at: str

    model_config = {"from_attributes": True}


# ─── AI generation schemas ────────────────────────────────────────────────────

class AiGenerateRequest(BaseModel):
    game_type: str           # wyr | nhi | hot_takes | quiz | build_date | emoji_story | truth_or_dare | question_cards
    theme: str = ""          # optional topic / mood hint from user
    chat_context: list[str] = []  # last N message texts for personalisation
    sub_type: str = ""       # for truth_or_dare: "truth" | "dare" (empty = random mix)


class AiGenerateOut(BaseModel):
    question: str            # the generated content (WYR: "OptionA|||OptionB")
    emoji: str
    color: str
    sub_type: str = ""       # for truth_or_dare: "truth" | "dare"


# ─── AI generation prompts per game type ──────────────────────────────────────

_GAME_PROMPTS: dict[str, str] = {
    "wyr": (
        "Generate a fun 'Would You Rather' dilemma for a dating app. "
        "Return JSON only: {{\"question\": \"Option A|||Option B\", \"emoji\": \"🤔\"}}. "
        "Make both options interesting. No markdown, just the JSON."
    ),
    "nhi": (
        "Generate a 'Never Have I Ever' statement for a dating app. "
        "Return JSON only: {{\"question\": \"...statement (no 'Never have I ever' prefix)\", \"emoji\": \"🍹\"}}. "
        "Keep it fun and slightly daring. Just JSON."
    ),
    "hot_takes": (
        "Generate a bold 'Hot Take' opinion for a dating app. "
        "Return JSON only: {{\"question\": \"...the hot take\", \"emoji\": \"🔥\"}}. "
        "It should be spicy and debatable. Just JSON."
    ),
    "quiz": (
        "Generate a compatibility quiz question for a dating app with 4 answer options. "
        "Return JSON only: {{\"question\": \"...question\", \"options\": [\"A\",\"B\",\"C\",\"D\"], \"emoji\": \"💘\"}}. "
        "Just JSON."
    ),
    "build_date": (
        "Generate a 'Build a Date' step for a dating app. "
        "Return JSON only: {{\"question\": \"...pick one: ...\", \"options\": [\"Option1\",\"Option2\",\"Option3\"], \"emoji\": \"📅\"}}. "
        "Just JSON."
    ),
    "emoji_story": (
        "Generate an emoji story starter for a dating app — a short sequence of emojis that tells a mini story. "
        "Return JSON only: {{\"question\": \"🏖️➡️🌅➡️...\", \"emoji\": \"📖\"}}. "
        "Just JSON."
    ),
    "truth_or_dare_truth": (
        "Generate a 'Truth' question for a Truth or Dare game on a dating app. "
        "It should be revealing, personal, and fun — not too invasive. "
        "Return JSON only: {{\"question\": \"...the truth question\", \"emoji\": \"🤫\", \"sub_type\": \"truth\"}}. "
        "Just JSON."
    ),
    "truth_or_dare_dare": (
        "Generate a 'Dare' task for a Truth or Dare game on a dating app. "
        "It should be fun, flirty, and doable over text/video. "
        "Return JSON only: {{\"question\": \"...the dare task\", \"emoji\": \"😈\", \"sub_type\": \"dare\"}}. "
        "Just JSON."
    ),
    "truth_or_dare": (
        "Generate a Truth or Dare card for a dating app. Randomly pick truth or dare. "
        "Return JSON only: {{\"question\": \"...the truth question or dare task\", \"emoji\": \"🎲\", \"sub_type\": \"truth\"}}. "
        "For dare use emoji 😈 and sub_type 'dare'. For truth use 🤫 and sub_type 'truth'. Just JSON."
    ),
    "question_cards": (
        "Generate a deep / fun conversation starter question for a dating app. "
        "Return JSON only: {{\"question\": \"...the question\", \"emoji\": \"💬\"}}. "
        "Just JSON."
    ),
}

_GAME_COLORS: dict[str, str] = {
    "wyr":           "#1e1b4b",
    "nhi":           "#78350f",
    "hot_takes":     "#7f1d1d",
    "quiz":          "#831843",
    "build_date":    "#14532d",
    "emoji_story":   "#0c4a6e",
    "truth_or_dare": "#312e81",
    "question_cards":"#1e3a5f",
}

_GAME_EMOJIS: dict[str, str] = {
    "wyr": "🤔", "nhi": "🍹", "hot_takes": "🔥",
    "quiz": "💘", "build_date": "📅", "emoji_story": "📖",
    "truth_or_dare": "🎲", "question_cards": "💬",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_card(c: Card) -> GameCardOut:
    """Enrich card with parsed option fields for structured game types."""
    out = GameCardOut(
        id=str(c.id),
        game=c.game,
        category=c.category,
        tag=c.tag,
        emoji=c.emoji,
        question=c.question,
        color=c.color,
        sort_order=c.sort_order,
    )
    # WYR: question = "Option A|||Option B"
    if c.game == 'wyr' and '|||' in c.question:
        parts = c.question.split('|||', 1)
        out.option_a = parts[0]
        out.option_b = parts[1]

    # Quiz/Date: question = "Prompt|||Opt1|||Opt2|||..."
    if c.game in ('quiz', 'build_date') and '|||' in c.question:
        parts = c.question.split('|||')
        out.question = parts[0]
        out.options = parts[1:]

    return out


# ─── Endpoints ───────────────────────────────────────────────────────────────

@mini_games_router.post("/generate", response_model=AiGenerateOut)
async def generate_mini_game_card(
    body: AiGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """AI-generate a card for any mini-game type. Falls back gracefully."""
    game_type = body.game_type
    theme     = body.theme.strip()
    sub_type  = body.sub_type.strip().lower()  # "truth" | "dare" | ""
    context   = body.chat_context[-10:]

    fallback_q: dict[str, str] = {
        "wyr":           "Travel to Paris alone|||Travel to Tokyo with a stranger",
        "nhi":           "stayed up all night just talking to someone",
        "hot_takes":     "Morning people are just evening people in denial",
        "quiz":          "What's your ideal first date?",
        "build_date":    "Pick the vibe: Beach sunset or City rooftop?",
        "emoji_story":   "🏖️➡️🌅➡️💃➡️🌙",
        "truth_or_dare": "What's the most spontaneous thing you've ever done?",
        "question_cards":"If you could relive one day of your life, which would it be?",
    }
    fallback_sub: dict[str, str] = {
        "truth_or_dare": sub_type or "truth",
    }

    default_q   = fallback_q.get(game_type, "Tell me something surprising about you!")
    default_emoji = _GAME_EMOJIS.get(game_type, "🎲")
    default_color = _GAME_COLORS.get(game_type, "#1e1b4b")
    default_sub = fallback_sub.get(game_type, "")

    if not settings.OPENAI_API_KEY:
        return AiGenerateOut(question=default_q, emoji=default_emoji, color=default_color, sub_type=default_sub)

    # Pick the right prompt key — truth_or_dare supports sub_type variants
    prompt_key = game_type
    if game_type == "truth_or_dare" and sub_type in ("truth", "dare"):
        prompt_key = f"truth_or_dare_{sub_type}"

    base_prompt = _GAME_PROMPTS.get(prompt_key, _GAME_PROMPTS["question_cards"])
    theme_hint  = f" Theme/mood: {theme}." if theme else ""
    context_block = ""
    if context:
        context_block = "\n\nRecent chat:\n" + "\n".join(f"- {m}" for m in context)

    user_prompt = (
        f"Game type: {game_type}.{theme_hint}{context_block}\n"
        "Make it feel personal and relevant."
    )

    try:
        import httpx
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": base_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "max_tokens": 150,
                    "temperature": 0.9,
                },
            )
        resp.raise_for_status()
        raw    = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        question = parsed.get("question", default_q)
        # For quiz/build_date the question may contain options list — pack them back
        if "options" in parsed and isinstance(parsed["options"], list):
            question = question + "|||" + "|||".join(parsed["options"])
        # Extract sub_type from parsed (truth_or_dare)
        result_sub = parsed.get("sub_type", sub_type or default_sub)
        return AiGenerateOut(
            question=question,
            emoji=parsed.get("emoji", default_emoji),
            color=default_color,
            sub_type=result_sub,
        )
    except Exception as exc:
        _log.warning("AI mini-game card generation failed: %s", exc)
        return AiGenerateOut(question=default_q, emoji=default_emoji, color=default_color, sub_type=default_sub)

@mini_games_router.get("", response_model=list[MiniGameOut])
async def list_mini_games(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all active mini-games in sort order."""
    rows = await db.execute(
        select(MiniGame)
        .where(MiniGame.is_active == True)
        .order_by(MiniGame.sort_order)
    )
    games = rows.scalars().all()
    return [
        MiniGameOut(
            id=str(g.id),
            game_type=g.game_type,
            name=g.name,
            tagline=g.tagline,
            description=g.description,
            emoji=g.emoji,
            accent_color=g.accent_color,
            bg_color=g.bg_color,
            categories=g.categories or [],
            sort_order=g.sort_order,
        )
        for g in games
    ]


@mini_games_router.get("/{game_type}/cards", response_model=list[GameCardOut])
async def get_game_cards(
    game_type: str,
    category: str | None = Query(default=None),
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all cards for a game type, optionally filtered by category."""
    q = select(Card).where(Card.game == game_type, Card.is_active == True)
    if category:
        q = q.where(Card.category == category)
    q = q.order_by(Card.sort_order)
    rows = await db.execute(q)
    cards = rows.scalars().all()
    return [_parse_card(c) for c in cards]


@mini_games_router.get("/{game_type}/cards/random", response_model=GameCardOut)
async def get_random_card(
    game_type: str,
    category: str | None = Query(default=None),
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns a single random card for a game type."""
    q = select(Card).where(Card.game == game_type, Card.is_active == True)
    if category:
        q = q.where(Card.category == category)
    rows = await db.execute(q)
    cards = rows.scalars().all()
    if not cards:
        raise HTTPException(status_code=404, detail="No cards found")
    return _parse_card(random.choice(cards))


@mini_games_router.post("/response", response_model=GameResponseOut)
async def save_response(
    body: GameResponseIn,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a player's response to a game move. Idempotent per (game_message_id, responder_id)."""
    # Check if this user already responded to this game message
    existing = await db.execute(
        select(GameResponse).where(
            GameResponse.game_message_id == body.game_message_id,
            GameResponse.responder_id == current_user.id,
        )
    )
    resp = existing.scalar_one_or_none()

    if resp:
        # Update existing response
        resp.response_data = body.response_data
    else:
        resp = GameResponse(
            id=uuid.uuid4(),
            game_message_id=body.game_message_id,
            game_type=body.game_type,
            room_id=body.room_id,
            responder_id=current_user.id,
            response_data=body.response_data,
        )
        db.add(resp)

    await db.commit()
    await db.refresh(resp)
    return GameResponseOut(
        id=str(resp.id),
        game_message_id=resp.game_message_id,
        game_type=resp.game_type,
        room_id=resp.room_id,
        responder_id=str(resp.responder_id),
        response_data=resp.response_data,
        created_at=resp.created_at.isoformat(),
    )


@mini_games_router.get("/responses/{room_id}", response_model=list[GameResponseOut])
async def get_room_responses(
    room_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all game responses in a chat room (for both players)."""
    rows = await db.execute(
        select(GameResponse)
        .where(GameResponse.room_id == room_id)
        .order_by(GameResponse.created_at)
    )
    responses = rows.scalars().all()
    return [
        GameResponseOut(
            id=str(r.id),
            game_message_id=r.game_message_id,
            game_type=r.game_type,
            room_id=r.room_id,
            responder_id=str(r.responder_id),
            response_data=r.response_data,
            created_at=r.created_at.isoformat(),
        )
        for r in responses
    ]


@mini_games_router.get("/response/{game_message_id}", response_model=list[GameResponseOut])
async def get_message_responses(
    game_message_id: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all responses for a specific game message (see both players' choices)."""
    rows = await db.execute(
        select(GameResponse)
        .where(GameResponse.game_message_id == game_message_id)
        .order_by(GameResponse.created_at)
    )
    responses = rows.scalars().all()
    return [
        GameResponseOut(
            id=str(r.id),
            game_message_id=r.game_message_id,
            game_type=r.game_type,
            room_id=r.room_id,
            responder_id=str(r.responder_id),
            response_data=r.response_data,
            created_at=r.created_at.isoformat(),
        )
        for r in responses
    ]
