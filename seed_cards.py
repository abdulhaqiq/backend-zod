"""
Seed question cards for the dating-app card system.

Run:  python seed_cards.py

Categories added:
  Question cards  → Deep, Fun, Romantic, Spicy, Would You Rather,
                    Dreams, Curious, Random
  Truth or Dare   → Truth, Dare  (kept as-is)
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ── adjust if your DATABASE_URL lives somewhere else ─────────────────────────
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from app.core.config import settings

DB_URL = settings.DATABASE_URL  # e.g. "postgresql+asyncpg://..."

# ─────────────────────────────────────────────────────────────────────────────

CARDS: list[dict] = []

def _add(game: str, category: str, tag: str, emoji: str,
         question: str, color: str, sort_order: int) -> None:
    CARDS.append(dict(
        id=str(uuid.uuid4()),
        game=game,
        category=category,
        tag=tag,
        emoji=emoji,
        question=question,
        color=color,
        sort_order=sort_order,
        is_active=True,
    ))


# ══════════════════════════════════════════════════════════════════════════════
#  QUESTION CARDS
# ══════════════════════════════════════════════════════════════════════════════

# ── Deep ─────────────────────────────────────────────────────────────────────
_DEEP_Q = [
    ("What's one belief you've held for years that you recently started questioning?", "🌀"),
    ("If your life had a theme song right now, what would it be?", "🎵"),
    ("What's the hardest thing you've ever had to forgive someone for?", "💔"),
    ("If you could relive one moment in your life, which would it be and why?", "⏳"),
    ("What's something you want to accomplish before you die that most people would never guess?", "🌠"),
    ("What's the most important lesson love has taught you?", "💡"),
    ("What does 'home' mean to you?", "🏡"),
    ("If you could talk to your 16-year-old self for 5 minutes, what would you say?", "🪞"),
    ("What's one secret fear you've never told anyone?", "🌑"),
    ("What's the biggest risk you've ever taken — was it worth it?", "🎲"),
    ("What do you think is your biggest blind spot?", "🔭"),
    ("At what point in your life did you feel most alive?", "✨"),
    ("What would you do differently if you knew nobody would judge you?", "🦋"),
    ("What's one thing you're still carrying from your childhood?", "🎒"),
    ("How do you know when you're in love?", "❤️"),
    ("What's something that scares you but excites you at the same time?", "⚡"),
    ("What does growing up mean to you — and have you?", "🌱"),
    ("If you could go back and change one decision, would you?", "🔄"),
]
for i, (q, e) in enumerate(_DEEP_Q):
    _add("question", "Deep", "Deep", e, q, "#1e1b4b", i)

# ── Fun ───────────────────────────────────────────────────────────────────────
_FUN_Q = [
    ("If you were a pizza topping, what would you be and why?", "🍕"),
    ("What's the most embarrassing thing that happened to you in public?", "😳"),
    ("What's the strangest thing you've Googled in the last week?", "🔍"),
    ("If you could swap lives with a celebrity for a day, who and why?", "⭐"),
    ("What's your most useless talent?", "🤹"),
    ("If your pet could talk, what would be their biggest complaint about you?", "🐾"),
    ("What's the weirdest thing you've ever eaten?", "🍜"),
    ("If you had to eat one food for the rest of your life, what would it be?", "🥑"),
    ("What's the funniest autocorrect fail you've had?", "📱"),
    ("If you were stranded on a desert island with only 3 apps, which would you pick?", "🏝️"),
    ("What's your go-to karaoke song?", "🎤"),
    ("If you had to star in a reality TV show, which one?", "📺"),
    ("What's the most ridiculous argument you've ever won?", "🏆"),
    ("If aliens visited Earth, what would you be embarrassed to explain to them?", "👽"),
    ("What's your most questionable fashion choice ever?", "👒"),
    ("What's the worst Wi-Fi name you've ever seen?", "📡"),
    ("If you were a meme, which one would you be?", "😂"),
    ("What's the most random thing you've impulse-bought?", "🛒"),
]
for i, (q, e) in enumerate(_FUN_Q):
    _add("question", "Fun", "Fun", e, q, "#3a2800", i)

# ── Romantic ──────────────────────────────────────────────────────────────────
_ROM_Q = [
    ("What's your idea of the perfect date?", "🕯️"),
    ("What small gesture makes you feel most loved?", "💌"),
    ("Do you believe in love at first sight?", "👀"),
    ("What's one thing that makes you feel truly seen by someone?", "🔮"),
    ("What song instantly puts you in a romantic mood?", "🎶"),
    ("What's the most thoughtful thing someone has ever done for you?", "🎁"),
    ("Would you rather have deep conversations or spontaneous adventures?", "🧭"),
    ("What's the most meaningful compliment you've ever received?", "💬"),
    ("What quality do you look for first in a partner?", "🌹"),
    ("What's something you'd want to do together on a rainy Sunday?", "☔"),
    ("How do you show love — words, actions, or something else?", "💞"),
    ("What's one experience you want to share with someone special?", "🌅"),
    ("What does your love language look like in practice?", "📖"),
    ("What would your dream morning look like with someone you love?", "☀️"),
    ("What's something simple that makes you feel deeply connected to someone?", "🤝"),
    ("What's the most romantic city you've ever been to?", "🗼"),
    ("What's your version of a perfect weekend together?", "💑"),
    ("What's one thing you'd do to surprise the person you love?", "🎀"),
]
for i, (q, e) in enumerate(_ROM_Q):
    _add("question", "Romantic", "Romantic", e, q, "#3d0a28", i)

# ── Spicy ─────────────────────────────────────────────────────────────────────
_SPICY_Q = [
    ("What's one rule you love to break?", "🔥"),
    ("What's the boldest thing you've ever done to get someone's attention?", "😏"),
    ("Have you ever done something and immediately thought, 'I can never tell anyone this'?", "🤫"),
    ("What's a deal-breaker for you that most people would find surprising?", "❌"),
    ("Have you ever sent a text to the wrong person — what was it?", "📲"),
    ("What's the most spontaneous thing you've ever done?", "⚡"),
    ("Have you ever lied to get out of a date?", "😅"),
    ("What's something you find attractive that you'd never admit out loud?", "👀"),
    ("What's the worst date you've ever been on?", "😬"),
    ("What's something you've always wanted to try but haven't had the courage to?", "💥"),
    ("What's your biggest pet peeve in dating?", "😤"),
    ("Have you ever stalked someone's social media before a date?", "🕵️"),
    ("What's one thing you'd never tell someone on a first date?", "🙊"),
    ("Have you ever been caught doing something you weren't supposed to?", "😰"),
    ("What's the most creative excuse you've ever made?", "🎭"),
    ("What's the most 'oops' moment you've had on a date?", "🙈"),
    ("What's your biggest turn-off that others find surprising?", "🚫"),
    ("What's a double standard you've noticed in dating?", "⚖️"),
]
for i, (q, e) in enumerate(_SPICY_Q):
    _add("question", "Spicy", "Spicy", e, q, "#3d0c0c", i)

# ── Would You Rather ─────────────────────────────────────────────────────────
_WYR_Q = [
    ("Would you rather know the date you'll die or how you'll die?", "💀"),
    ("Would you rather have perfect memory or the ability to forget painful things?", "🧠"),
    ("Would you rather travel 100 years into the past or 100 years into the future?", "⏱️"),
    ("Would you rather be always early or always exactly on time?", "⏰"),
    ("Would you rather lose your phone or your wallet?", "📵"),
    ("Would you rather have one true love or 10 great friendships?", "🤍"),
    ("Would you rather only speak in song lyrics or movie quotes?", "🎬"),
    ("Would you rather read minds or be invisible?", "🔮"),
    ("Would you rather live in a city or the countryside?", "🌆"),
    ("Would you rather be famous now but forgotten, or unknown now but remembered forever?", "🏛️"),
    ("Would you rather have no internet for a month or no coffee?", "☕"),
    ("Would you rather know all languages or play every instrument?", "🎸"),
    ("Would you rather always tell the truth or lie without consequence?", "🃏"),
    ("Would you rather have a photographic memory or sleep only 4 hours?", "😴"),
    ("Would you rather explore the ocean or outer space?", "🚀"),
    ("Would you rather never be cold or never be too hot?", "🌡️"),
    ("Would you rather eat your favourite meal every day or never eat it again?", "🍽️"),
    ("Would you rather have a rewind button or a pause button for your life?", "⏸️"),
]
for i, (q, e) in enumerate(_WYR_Q):
    _add("question", "Would You Rather", "WYR", e, q, "#2d1a5e", i)

# ── Dreams ────────────────────────────────────────────────────────────────────
_DREAM_Q = [
    ("What does your dream life look like in 10 years?", "🌟"),
    ("If money were no object, how would you spend your days?", "💸"),
    ("What's one country you'd drop everything to live in?", "🌍"),
    ("What's something you've always wanted to create?", "🎨"),
    ("If you could master any skill overnight, what would it be?", "⚡"),
    ("What's the most adventurous thing on your bucket list?", "🗺️"),
    ("If you could wake up tomorrow having gained one ability, what would it be?", "🦸"),
    ("What does success look like to you — really?", "🏆"),
    ("If you were to write a book, what would it be about?", "📝"),
    ("What's one problem in the world you'd fix if you could?", "🌐"),
    ("What legacy do you want to leave behind?", "🕊️"),
    ("If you could spend a year anywhere doing anything, what would you choose?", "🌏"),
    ("What's one thing you'd do if you knew you couldn't fail?", "🚀"),
    ("If you could start any business right now, what would it be?", "💡"),
    ("Where do you see yourself in 5 years — personally, not professionally?", "🔭"),
    ("If you could collaborate with anyone alive, who would it be?", "🤝"),
    ("What's one thing you haven't done yet that you're proud of planning?", "📅"),
    ("What would 'enough' look like in your life?", "⚖️"),
]
for i, (q, e) in enumerate(_DREAM_Q):
    _add("question", "Dreams", "Dreams", e, q, "#003040", i)

# ── Curious ───────────────────────────────────────────────────────────────────
_CUR_Q = [
    ("What's a topic you could talk about for hours without getting bored?", "🔬"),
    ("If you could have dinner with any historical figure, who and why?", "🏛️"),
    ("What do you think happens after we die?", "🌌"),
    ("What's the best piece of advice you've ever received?", "💬"),
    ("What book or movie changed how you see the world?", "📚"),
    ("What's something most people get wrong about you?", "🙃"),
    ("If you could live in any time period, which would you choose?", "⏳"),
    ("What's a conspiracy theory you lowkey believe?", "🕵️"),
    ("What's the most fascinating thing you learned this year?", "💡"),
    ("If you could go back and study anything in school, what would it be?", "🎓"),
    ("What habit has changed your life the most?", "🔄"),
    ("What's something you know a lot about that surprises people?", "🤯"),
    ("What's a question you've always wanted to ask but haven't?", "❓"),
    ("What's one thing technology will change in the next 10 years?", "🤖"),
    ("What's the hardest concept you've ever had to wrap your mind around?", "🧩"),
    ("Do you think free will exists?", "🎭"),
    ("What's one thing you unlearned as an adult?", "🔓"),
    ("What's your favourite 'shower thought'?", "🚿"),
]
for i, (q, e) in enumerate(_CUR_Q):
    _add("question", "Curious", "Curious", e, q, "#0d2e1a", i)

# ── Random ────────────────────────────────────────────────────────────────────
_RND_Q = [
    ("If you could have any superpower but only for Tuesdays, what would it be?", "🦸"),
    ("What would your theme song be if your life were a movie?", "🎬"),
    ("If your personality were a weather pattern, what would it be?", "⛅"),
    ("What's the most random skill you have that nobody knows about?", "🤹"),
    ("If you were a board game, which one would you be?", "🎯"),
    ("If your pet wrote a Yelp review of you, what would it say?", "⭐"),
    ("What's the most random fact you know?", "🧠"),
    ("If you were a kitchen appliance, which would you be?", "🍳"),
    ("What fictional universe would you most want to live in?", "🌀"),
    ("If you could swap jobs with anyone for a week, who?", "🔄"),
    ("What's your hot take on something completely trivial?", "🌶️"),
    ("If you were a cocktail, what would be in it?", "🍹"),
    ("What animal best represents your current mood?", "🦊"),
    ("If your morning routine were a genre of music, what would it be?", "🎵"),
    ("What's your strangest deal-breaker?", "🚩"),
    ("If you were a font, which one would you be?", "🔤"),
    ("What's the most niche thing you're weirdly passionate about?", "💫"),
    ("If you could add one thing to the periodic table, what would it be?", "⚗️"),
]
for i, (q, e) in enumerate(_RND_Q):
    _add("question", "Random", "Random", e, q, "#1a2535", i)


# ══════════════════════════════════════════════════════════════════════════════
#  TRUTH OR DARE
# ══════════════════════════════════════════════════════════════════════════════

# ── Truths ────────────────────────────────────────────────────────────────────
_TRUTH_Q = [
    ("What's the most rebellious thing you've ever done?", "😈"),
    ("Have you ever had a crush on a friend's partner?", "😳"),
    ("What's the biggest lie you've ever told?", "🤥"),
    ("What's the most embarrassing thing in your search history?", "🔍"),
    ("Have you ever ghosted someone? What happened?", "👻"),
    ("What's something you've done that you hope your parents never find out?", "🙈"),
    ("What's the pettiest thing you've done after a breakup?", "💅"),
    ("Have you ever eavesdropped on a conversation?", "👂"),
    ("What's one thing you pretend you don't care about but actually do?", "🎭"),
    ("What's the most childish thing you still do?", "🧸"),
    ("Have you ever taken credit for someone else's idea?", "🏅"),
    ("What's the boldest pick-up line you've ever used?", "😏"),
    ("What's something you've never told your best friend?", "🤐"),
    ("When did you last cry and why?", "😢"),
    ("What's a secret you've kept for over 5 years?", "🔒"),
    ("What's the most dramatic thing you've done to get someone's attention?", "🎪"),
    ("Have you ever faked being sick to avoid something?", "🤒"),
    ("What's your most irrational fear?", "😱"),
]
for i, (q, e) in enumerate(_TRUTH_Q):
    _add("truth_or_dare", "Truth", "Truth", e, q, "#312e81", i)

# ── Dares ─────────────────────────────────────────────────────────────────────
_DARE_Q = [
    ("Send me a photo of your current surroundings — no tidying up!", "📸"),
    ("Write me a 2-line poem about our conversation right now.", "✍️"),
    ("Send a voice note doing your best impression of a movie villain.", "🎙️"),
    ("Tell me something true about me that you've been thinking.", "💬"),
    ("Send me the most chaotic photo from your camera roll.", "🖼️"),
    ("Do your best runway walk across the room and describe it.", "💃"),
    ("Send a voice note singing one line of your favourite song.", "🎤"),
    ("Make a list of 5 things you like about me — right now.", "📝"),
    ("Send me a screenshot of the last song you listened to.", "🎵"),
    ("Text someone you haven't spoken to in over a year — right now.", "📱"),
    ("Do 10 jumping jacks and send me proof.", "🏃"),
    ("Write a fake ad for yourself in 3 sentences.", "📣"),
    ("Send me a selfie with the silliest face you can make.", "🤳"),
    ("Share the last meme you sent to anyone.", "😂"),
    ("Rate our conversation out of 10 and explain your score.", "⭐"),
    ("Show me the most embarrassing app on your phone.", "📱"),
    ("Send me a voice note describing your ideal Saturday.", "☀️"),
    ("Make up a new word and use it in a sentence.", "📖"),
]
for i, (q, e) in enumerate(_DARE_Q):
    _add("truth_or_dare", "Dare", "Dare", e, q, "#7f1d1d", i)


# ══════════════════════════════════════════════════════════════════════════════
#  DB WRITE
# ══════════════════════════════════════════════════════════════════════════════

async def seed() -> None:
    engine = create_async_engine(DB_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Remove existing cards (optional: comment out to append instead)
        await session.execute(text("DELETE FROM cards"))
        await session.commit()

        for chunk in [CARDS[i:i+50] for i in range(0, len(CARDS), 50)]:
            await session.execute(
                text("""
                    INSERT INTO cards (id, game, category, tag, emoji, question, color, sort_order, is_active)
                    VALUES (:id, :game, :category, :tag, :emoji, :question, :color, :sort_order, :is_active)
                """),
                chunk,
            )
        await session.commit()

    await engine.dispose()
    print(f"✅  Seeded {len(CARDS)} cards across {len(set(c['category'] for c in CARDS))} categories.")


if __name__ == "__main__":
    asyncio.run(seed())
