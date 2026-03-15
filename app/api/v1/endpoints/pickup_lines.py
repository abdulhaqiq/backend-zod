"""
Pickup Lines API

GET  /api/v1/pickup-lines/categories          — list all categories
GET  /api/v1/pickup-lines                     — list all lines (optionally filter by category)
POST /api/v1/pickup-lines/generate            — AI-generate a pickup line
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.pickup_line import PickupLine

_log = logging.getLogger(__name__)

pickup_lines_router = APIRouter(prefix="/pickup-lines", tags=["pickup-lines"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PickupLineOut(BaseModel):
    id: str
    category: str
    line: str
    emoji: str
    sort_order: int

    model_config = {"from_attributes": True}


class AiPickupRequest(BaseModel):
    category: str = "Classic"
    custom_prompt: str = ""          # optional extra context/instruction
    match_name: str = ""


class AiPickupOut(BaseModel):
    lines: list[str]


# ── Category metadata ────────────────────────────────────────────────────────

CATEGORY_META: dict[str, dict] = {
    "Classic":     {"emoji": "✨", "color": "#6366f1", "desc": "Timeless & charming"},
    "Cheesy":      {"emoji": "🧀", "color": "#f59e0b", "desc": "Delightfully corny"},
    "Romantic":    {"emoji": "💕", "color": "#ec4899", "desc": "Sweet & heartfelt"},
    "Nerdy":       {"emoji": "🤓", "color": "#3b82f6", "desc": "Geeky & clever"},
    "Adventurous": {"emoji": "✈️", "color": "#10b981", "desc": "Bold & exciting"},
    "Deep":        {"emoji": "🌊", "color": "#7c3aed", "desc": "Thoughtful & soulful"},
    "Funny":       {"emoji": "😂", "color": "#f97316", "desc": "Make them laugh"},
    "Smooth":      {"emoji": "😏", "color": "#ef4444", "desc": "Confident & suave"},
}

_FALLBACK_LINES: dict[str, list[str]] = {
    "Classic":     [
        "Are you a magician? Because whenever I look at you, everyone else disappears.",
        "Do you have a map? I keep getting lost in your eyes.",
        "Is your name Wi-Fi? Because I'm feeling a connection.",
    ],
    "Cheesy":      [
        "Are you a parking ticket? Because you've got 'fine' written all over you.",
        "Are you a campfire? Because you're hot and I want s'more.",
        "Do you believe in love at first swipe?",
    ],
    "Romantic":    [
        "If I could rearrange the alphabet, I'd put U and I together.",
        "You must be the reason the stars shine a little brighter tonight.",
        "Every love story is beautiful, but ours could be my favourite.",
    ],
    "Nerdy":       [
        "Are you made of copper and tellurium? Because you're CuTe.",
        "You must be a 90° angle, because you're looking right.",
        "Do you have 11 protons? Because you're sodium fine.",
    ],
    "Adventurous": [
        "If travel were a language, we'd be fluent ✈️",
        "I've been to 14 countries and none of them were as interesting as this conversation.",
        "They say the best adventures are unplanned — like meeting you 🛫",
    ],
    "Deep":        [
        "My future self sent me a note — it said I had to talk to you.",
        "You know, they say the universe is constantly expanding. I think it's because it's making more room for people like you.",
        "I don't believe in coincidences. I think we were supposed to meet.",
    ],
    "Funny":       [
        "I was going to play it cool, but your profile made that impossible 😅",
        "Is your name Google? Because you have everything I've been searching for.",
        "I must be a snowflake, because I've fallen for you.",
    ],
    "Smooth":      [
        "You must be a great destination — everyone wants to go there 🌍",
        "Are you a great book? Because I can't stop thinking about your story 📚",
        "I was going to say something smooth, but you left me speechless.",
    ],
}


# ── Routes ────────────────────────────────────────────────────────────────────

@pickup_lines_router.get("/categories", response_model=list[dict])
async def list_categories(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all active categories with metadata."""
    rows = await db.execute(
        select(PickupLine.category)
        .where(PickupLine.is_active == True)
        .distinct()
        .order_by(PickupLine.category)
    )
    db_cats = {r[0] for r in rows.all()}

    # Return all defined categories, mark which have DB content
    result = []
    for cat, meta in CATEGORY_META.items():
        result.append({
            "category": cat,
            "emoji": meta["emoji"],
            "color": meta["color"],
            "desc": meta["desc"],
            "has_db_content": cat in db_cats,
        })
    return result


@pickup_lines_router.get("", response_model=list[PickupLineOut])
async def list_lines(
    category: str | None = Query(default=None),
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns pickup lines, optionally filtered by category."""
    q = select(PickupLine).where(PickupLine.is_active == True)
    if category:
        q = q.where(PickupLine.category == category)
    q = q.order_by(PickupLine.sort_order)

    rows = await db.execute(q)
    lines = rows.scalars().all()

    if lines:
        return [
            PickupLineOut(
                id=str(pl.id),
                category=pl.category,
                line=pl.line,
                emoji=pl.emoji,
                sort_order=pl.sort_order,
            )
            for pl in lines
        ]

    # Fallback to hardcoded lines when DB has no seeded data
    cat = category or "Classic"
    fallbacks = _FALLBACK_LINES.get(cat, _FALLBACK_LINES["Classic"])
    cat_emoji = CATEGORY_META.get(cat, {}).get("emoji", "✨")
    return [
        PickupLineOut(id=f"fallback-{i}", category=cat, line=line, emoji=cat_emoji, sort_order=i)
        for i, line in enumerate(fallbacks)
    ]


@pickup_lines_router.post("/generate", response_model=AiPickupOut)
async def generate_ai_pickup_lines(
    body: AiPickupRequest,
    _: dict = Depends(get_current_user),
):
    """AI-generates 5 personalised pickup lines."""
    category    = body.category
    match_name  = body.match_name.strip()
    custom_note = body.custom_prompt.strip()

    fallbacks = _FALLBACK_LINES.get(category, _FALLBACK_LINES["Classic"])

    if not settings.OPENAI_API_KEY:
        return AiPickupOut(lines=fallbacks[:5])

    try:
        import httpx

        name_bit   = f" tailored for someone named {match_name}" if match_name else ""
        custom_bit = f"\n\nExtra instruction: {custom_note}" if custom_note else ""
        cat_desc   = CATEGORY_META.get(category, {}).get("desc", category)

        system_prompt = (
            "You are a witty, charming pickup line writer for a dating app. "
            "Generate exactly 5 unique, creative pickup lines that feel natural and fun — never offensive. "
            "Return ONLY a JSON array of 5 strings, nothing else. No markdown, no explanation."
        )
        user_prompt = (
            f"Write 5 {category} pickup lines ({cat_desc}){name_bit}.{custom_bit}\n"
            "Return as a JSON array: [\"line1\", \"line2\", \"line3\", \"line4\", \"line5\"]"
        )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "max_tokens": 300,
                    "temperature": 0.9,
                },
            )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) >= 1:
            return AiPickupOut(lines=[str(l) for l in parsed[:5]])

    except Exception as exc:
        _log.warning("AI pickup line generation failed: %s", exc)

    return AiPickupOut(lines=fallbacks[:5])
