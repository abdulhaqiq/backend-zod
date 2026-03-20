"""
Pickup Lines API

GET  /api/v1/pickup-lines/categories          — list all active categories from DB
GET  /api/v1/pickup-lines?category=X          — list lines for a category from DB
POST /api/v1/pickup-lines/generate            — AI-generate personalised lines
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
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
    "Classic": [
        "Are you a magician? Because whenever I look at you, everyone else disappears.",
        "Do you have a map? I keep getting lost in your eyes.",
        "Is your name Wi-Fi? Because I'm feeling a connection.",
        "Do you have a Band-Aid? Because I just scraped my knee falling for you.",
        "Are you a parking ticket? You've got 'fine' written all over you.",
        "If you were a vegetable, you'd be a cute-cumber.",
        "Do you believe in love at first sight, or should I walk by again?",
        "Is your name Google? Because you've got everything I've been searching for.",
        "Are you a time traveller? Because I can see you in my future.",
        "If being beautiful were a crime, you'd definitely be doing life.",
    ],
    "Cheesy": [
        "Are you a campfire? Because you're hot and I want s'more.",
        "Do you believe in love at first swipe?",
        "Are you a keyboard? Because you're just my type.",
        "Do you have a mirror in your pocket? Because I can see myself in your pants.",
        "Are you a bank loan? Because you've got my interest.",
        "Is your dad a baker? Because you're a cutie pie.",
        "Do you like raisins? How do you feel about a date?",
        "Are you a magnet? Because I'm feeling a real attraction here.",
        "Is your name Chapstick? Because you're da balm.",
        "Are you made of cheese? Because you're looking Gouda to me.",
    ],
    "Romantic": [
        "If I could rearrange the alphabet, I'd put U and I together.",
        "You must be the reason the stars shine a little brighter tonight.",
        "Every love story is beautiful, but ours could be my favourite.",
        "In a room full of art, I'd still stare at you.",
        "I think my heart just skipped a beat — and I'm pretty sure it's your fault.",
        "You're the first thing I think about when someone says 'good things come in small packages' — but you're a whole universe.",
        "If kisses were snowflakes, I'd send you a blizzard.",
        "You make me want to write poetry — and I've never written a single line.",
        "I don't need a wish upon a star; somehow I already found you.",
        "Meeting you has been the best unplanned thing in my life so far.",
    ],
    "Nerdy": [
        "Are you made of copper and tellurium? Because you're CuTe.",
        "You must be a 90° angle, because you're looking right.",
        "Do you have 11 protons? Because you're sodium fine.",
        "Are you a black hole? Because time stops when I'm near you.",
        "Are you a compiler? Because you make my heart run without any errors.",
        "My love for you has O(1) complexity — it's constant and instant.",
        "You must be the square root of -1, because you can't be real.",
        "If you were a function, you'd be continuous — because I can't see any breaks.",
        "Are you HTTP? Because without you I'm just //.",
        "You must be made of dark matter — invisible to most, but I feel your pull everywhere.",
    ],
    "Adventurous": [
        "If travel were a language, we'd be fluent ✈️",
        "I've been to 14 countries and none of them were as interesting as this conversation.",
        "They say the best adventures are unplanned — like meeting you 🛫",
        "I was about to book a solo trip, then I thought: wouldn't it be better with two? 🌍",
        "Every great story starts with a bold first step — this message is mine.",
        "I can read a map in 5 languages but still found myself lost in your profile.",
        "I've jumped off cliffs, swum with sharks, and hiked at 4am — but messaging you first is somehow scarier.",
        "I think you might be my next favourite destination 🏝️",
        "They say home is where the heart is. Apparently mine's here now.",
        "Let's skip the small talk and plan something worth remembering 🗺️",
    ],
    "Deep": [
        "My future self sent me a note — it said I had to talk to you.",
        "You know, the universe is constantly expanding. I think it's making more room for moments like this.",
        "I don't believe in coincidences. I think we were supposed to meet.",
        "There are 8 billion people on this planet. The fact that we crossed paths feels like more than chance.",
        "If souls recognise each other across lifetimes, I think mine knows yours.",
        "I'm not looking for someone to complete me — just someone to explore life alongside. Somehow I think you get that.",
        "I've been asking myself what I really want. And then I saw your profile, and the question got quieter.",
        "Most conversations are noise. I have a feeling ours would be different.",
        "Every person I meet teaches me something. I'm curious what you'd teach me.",
        "You seem like someone who has really thought about their life. That's rarer than it sounds.",
    ],
    "Funny": [
        "I was going to play it cool, but your profile made that impossible 😅",
        "I must be a snowflake, because I've fallen for you.",
        "I tried to think of a clever opening line, but my brain short-circuited after seeing your photos.",
        "Quick question: are you always this attractive, or did you just install a filter on reality?",
        "Warning: this message may cause uncontrollable smiling.",
        "I was today years old when I realised I had terrible taste in everything except this swipe.",
        "My therapist says I need to put myself out there more. So hi 👋 You're welcome, Dr. Ahmed.",
        "Legend has it if you message first, a puppy gets its wings. I'm just doing my part.",
        "I've been practising this opening line for 20 minutes. It was 'hey'. Worth it.",
        "I have to ask — do you always look this good or is there a special occasion?",
    ],
    "Smooth": [
        "You must be a great destination — everyone wants to go there 🌍",
        "Are you a great book? Because I can't stop thinking about your story 📚",
        "I was going to say something smooth, but you left me speechless.",
        "I don't usually message first — but something about you felt worth breaking the habit.",
        "I've seen a lot of profiles. Yours actually made me stop scrolling.",
        "You seem like the kind of person who makes everywhere you go a little better.",
        "I have a good feeling about this conversation. Let's see if I'm right.",
        "Forgive me if this sounds forward — but I think we'd have a really good time talking.",
        "I don't know your story yet, but I already think it's interesting.",
        "Most people are predictable. You don't seem to be. That's rare.",
    ],
}


# ── Routes ────────────────────────────────────────────────────────────────────

@pickup_lines_router.get("/categories", response_model=list[dict])
async def list_categories(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all active categories from the DB, enriched with metadata.
    Each row includes category name, emoji, color, description, and line count.
    """
    # Fetch distinct categories + counts in one query
    rows = await db.execute(
        select(
            PickupLine.category,
            PickupLine.emoji,
            func.count(PickupLine.id).label("count"),
        )
        .where(PickupLine.is_active == True)
        .group_by(PickupLine.category, PickupLine.emoji)
        .order_by(PickupLine.category)
    )
    db_cats = {r[0]: {"emoji": r[1], "count": r[2]} for r in rows.all()}

    # Build response preserving CATEGORY_META order; include only categories in DB
    result = []
    for cat, meta in CATEGORY_META.items():
        if cat in db_cats:
            result.append({
                "category":  cat,
                "emoji":     db_cats[cat]["emoji"] or meta["emoji"],
                "color":     meta["color"],
                "desc":      meta["desc"],
                "line_count": db_cats[cat]["count"],
            })

    # If DB is empty (before seeding), fall back to metadata list
    if not result:
        result = [
            {"category": cat, "emoji": meta["emoji"], "color": meta["color"],
             "desc": meta["desc"], "line_count": 0}
            for cat, meta in CATEGORY_META.items()
        ]

    return result


@pickup_lines_router.get("", response_model=list[PickupLineOut])
async def list_lines(
    category: str | None = Query(default=None),
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all active pickup lines for the given category from the DB,
    ordered by sort_order. Falls back to _FALLBACK_LINES only if the DB
    table is completely empty (i.e. seed_pickup_lines.py hasn't been run yet).
    """
    q = (
        select(PickupLine)
        .where(PickupLine.is_active == True)
        .order_by(PickupLine.sort_order)
    )
    if category:
        q = q.where(PickupLine.category == category)

    rows = await db.execute(q)
    lines = list(rows.scalars().all())

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

    # DB not seeded yet — return in-memory fallback so app stays functional
    _log.warning("pickup_lines table empty; serving fallback lines. Run seed_pickup_lines.py.")
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

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "max_tokens": 180,
                    "temperature": 0.85,
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
