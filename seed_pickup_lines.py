"""
Seed pickup lines into the database.

Run:  python seed_pickup_lines.py

Clears existing pickup_lines rows and inserts fresh ones for all 8 categories.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from app.core.config import settings

DB_URL = settings.DATABASE_URL

# ── Lines per category ────────────────────────────────────────────────────────

PICKUP_LINES: dict[str, dict] = {
    "Classic": {
        "emoji": "✨",
        "lines": [
            "Are you a magician? Because whenever I look at you, everyone else disappears.",
            "Do you have a map? I keep getting lost in your eyes.",
            "Is your name Wi-Fi? Because I'm feeling a connection.",
            "Do you have a Band-Aid? Because I just scraped my knee falling for you.",
            "If being beautiful were a crime, you'd definitely be doing life.",
            "Do you believe in love at first sight, or should I walk by again?",
            "Is your name Google? Because you've got everything I've been searching for.",
            "Are you a time traveller? Because I can see you in my future.",
            "If you were a vegetable, you'd be a cute-cumber.",
            "Are you a star? Because you light up every room you're in.",
            "I seem to have lost my phone number — can I have yours?",
            "Do you have a name, or can I call you mine?",
        ],
    },
    "Cheesy": {
        "emoji": "🧀",
        "lines": [
            "Are you a campfire? Because you're hot and I want s'more.",
            "Do you believe in love at first swipe?",
            "Are you a keyboard? Because you're just my type.",
            "Is your dad a baker? Because you're a cutie pie.",
            "Do you like raisins? How do you feel about a date?",
            "Are you a bank loan? Because you've got my interest.",
            "Is your name Chapstick? Because you're da balm.",
            "Are you made of cheese? Because you're looking Gouda to me.",
            "Are you a magnet? Because I'm feeling a real attraction here.",
            "Are you French? Because Eiffel for you.",
            "You must be tired — you've been running through my mind all day.",
            "I'd say you're the bomb, but that could turn into an explosive situation.",
        ],
    },
    "Romantic": {
        "emoji": "💕",
        "lines": [
            "If I could rearrange the alphabet, I'd put U and I together.",
            "You must be the reason the stars shine a little brighter tonight.",
            "Every love story is beautiful, but ours could be my favourite.",
            "In a room full of art, I'd still stare at you.",
            "I think my heart just skipped a beat — and I'm pretty sure it's your fault.",
            "If kisses were snowflakes, I'd send you a blizzard.",
            "You make me want to write poetry — and I've never written a single line.",
            "I don't need a wish upon a star; somehow I already found you.",
            "Meeting you has been the best unplanned thing in my life so far.",
            "You're the first thing I think about when I hear the word 'wonderful'.",
            "I never believed in fate until your profile appeared on my screen.",
            "If my heart were a compass, it would always point to you.",
        ],
    },
    "Nerdy": {
        "emoji": "🤓",
        "lines": [
            "Are you made of copper and tellurium? Because you're CuTe.",
            "You must be a 90° angle, because you're looking right.",
            "Do you have 11 protons? Because you're sodium fine.",
            "Are you a black hole? Because time stops when I'm near you.",
            "Are you a compiler? Because you make my heart run without errors.",
            "My love for you has O(1) complexity — constant and instant.",
            "You must be the square root of -1, because you can't be real.",
            "If you were a function, you'd be continuous — I can't find any breaks.",
            "Are you HTTP? Because without you I'm just //.",
            "You must be made of dark matter — invisible to most, but I feel your pull.",
            "Are you a neural network? Because you've got layers I can't stop exploring.",
            "Is your name Pascal? Because you raise my spirits.",
        ],
    },
    "Adventurous": {
        "emoji": "✈️",
        "lines": [
            "If travel were a language, we'd be fluent ✈️",
            "I've been to 14 countries and none of them were as interesting as this conversation.",
            "They say the best adventures are unplanned — like meeting you 🛫",
            "I was about to book a solo trip, then I thought: wouldn't it be better with two? 🌍",
            "Every great story starts with a bold first step — this message is mine.",
            "I've jumped off cliffs, swum with sharks, and hiked at 4am — but messaging you is somehow scarier.",
            "I think you might be my next favourite destination 🏝️",
            "Let's skip the small talk and plan something worth remembering 🗺️",
            "I can read a map in five languages but still got lost in your profile.",
            "They say home is where the heart is. Apparently mine's here now.",
            "I collect passport stamps. I think collecting memories with you would be better.",
            "You seem like someone whose company would make any destination better.",
        ],
    },
    "Deep": {
        "emoji": "🌊",
        "lines": [
            "My future self sent me a note — it said I had to talk to you.",
            "The universe is constantly expanding. I think it's making more room for moments like this.",
            "I don't believe in coincidences. I think we were supposed to meet.",
            "There are 8 billion people on this planet. The fact that we crossed paths feels like more than chance.",
            "If souls recognise each other across lifetimes, I think mine knows yours.",
            "I'm not looking for someone to complete me — just someone to explore life alongside.",
            "Most conversations are noise. I have a feeling ours would be different.",
            "Every person I meet teaches me something. I'm curious what you'd teach me.",
            "You seem like someone who has really thought about their life. That's rarer than it sounds.",
            "I've been asking myself what I really want lately. Then I saw your profile, and the question got quieter.",
            "There's a version of me that almost didn't open this app tonight. I'm glad that version lost.",
            "What would you do if you weren't afraid? I'd start by saying hi to you.",
        ],
    },
    "Funny": {
        "emoji": "😂",
        "lines": [
            "I was going to play it cool, but your profile made that impossible 😅",
            "I must be a snowflake, because I've fallen for you.",
            "I tried to think of a clever opening line, but my brain short-circuited after seeing your photos.",
            "Quick question: are you always this attractive, or did you install a filter on reality?",
            "Warning: this message may cause uncontrollable smiling.",
            "I was today years old when I realised I had terrible taste in everything except this swipe.",
            "My therapist says I need to put myself out there more. So hi 👋 You're welcome.",
            "I've been practising this opening line for 20 minutes. It was 'hey'. Worth it.",
            "I have to ask — do you always look this good or is there a special occasion?",
            "Legend has it if you message first, a puppy gets its wings. I'm just doing my part.",
            "I told my dog about you. He said I should message you. He's rarely wrong.",
            "I solemnly swear I am up to no good. But also genuinely interested.",
        ],
    },
    "Smooth": {
        "emoji": "😏",
        "lines": [
            "You must be a great destination — everyone wants to go there 🌍",
            "I don't usually message first — but something about you felt worth breaking the habit.",
            "I was going to say something smooth, but you left me speechless.",
            "I've seen a lot of profiles. Yours actually made me stop scrolling.",
            "You seem like the kind of person who makes everywhere you go a little better.",
            "I have a good feeling about this conversation. Let's see if I'm right.",
            "I don't know your story yet, but I already think it's interesting.",
            "Most people are predictable. You don't seem to be. That's rare.",
            "Forgive me if this sounds forward — but I think we'd have a really good time talking.",
            "You seem like someone who's hard to impress. I appreciate the challenge.",
            "I'll be honest — I almost scrolled past. My instincts told me not to. I trust them.",
            "There's confident, and then there's you. It's a good difference.",
        ],
    },
}

# ── Seed runner ───────────────────────────────────────────────────────────────

async def seed() -> None:
    engine = create_async_engine(DB_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    rows = []
    for cat, meta in PICKUP_LINES.items():
        for i, line in enumerate(meta["lines"]):
            rows.append({
                "id":         str(uuid.uuid4()),
                "category":   cat,
                "line":       line,
                "emoji":      meta["emoji"],
                "sort_order": i,
                "is_active":  True,
            })

    async with async_session() as session:
        await session.execute(text("DELETE FROM pickup_lines"))
        await session.commit()

        for chunk in [rows[i:i+50] for i in range(0, len(rows), 50)]:
            await session.execute(
                text("""
                    INSERT INTO pickup_lines (id, category, line, emoji, sort_order, is_active)
                    VALUES (:id, :category, :line, :emoji, :sort_order, :is_active)
                """),
                chunk,
            )
        await session.commit()

    await engine.dispose()
    total = sum(len(m["lines"]) for m in PICKUP_LINES.values())
    print(f"✅  Seeded {total} pickup lines across {len(PICKUP_LINES)} categories.")


if __name__ == "__main__":
    asyncio.run(seed())
