"""
Seed marketing countries (with IANA timezones + peak hours) and initial
multilingual templates into the database.

Run:
    cd backend
    python seed_marketing.py

Safe to run multiple times — uses INSERT ... ON CONFLICT DO NOTHING for countries
and only inserts templates when the table is empty.
"""
import asyncio
import os
import sys

# Allow running from the backend/ directory
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/zod",
)

engine = create_async_engine(DATABASE_URL, echo=False)
Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ── Country seed data ──────────────────────────────────────────────────────────
# Each entry: (name, code, region, tz_name, peak_hours_json, primary_language)
# US / Canada have multiple rows (one per timezone).
# India has 6 peak hours as requested.

COUNTRIES = [
    # ── GCC ───────────────────────────────────────────────────────────────────
    ("Saudi Arabia",          "SA", "GCC",    "Asia/Riyadh",      [8, 12, 20, 21], "ar"),
    ("United Arab Emirates",  "AE", "GCC",    "Asia/Dubai",       [8, 12, 20, 21], "ar"),
    ("Qatar",                 "QA", "GCC",    "Asia/Qatar",       [8, 12, 20, 21], "ar"),
    ("Kuwait",                "KW", "GCC",    "Asia/Kuwait",      [8, 12, 20, 21], "ar"),
    ("Bahrain",               "BH", "GCC",    "Asia/Bahrain",     [8, 12, 20, 21], "ar"),
    ("Oman",                  "OM", "GCC",    "Asia/Muscat",      [8, 12, 20, 21], "ar"),

    # ── India (6 peak hours as requested) ─────────────────────────────────────
    ("India",                 "IN", "India",  "Asia/Kolkata",     [7, 9, 12, 18, 20, 22], "hi"),

    # ── MENA ──────────────────────────────────────────────────────────────────
    ("Egypt",                 "EG", "MENA",   "Africa/Cairo",     [9, 12, 19, 21], "ar"),
    ("Morocco",               "MA", "MENA",   "Africa/Casablanca",[9, 12, 19, 21], "ar"),
    ("Tunisia",               "TN", "MENA",   "Africa/Tunis",     [9, 12, 19, 21], "ar"),
    ("Algeria",               "DZ", "MENA",   "Africa/Algiers",   [9, 12, 19, 21], "ar"),
    ("Libya",                 "LY", "MENA",   "Africa/Tripoli",   [9, 12, 19, 21], "ar"),
    ("Jordan",                "JO", "MENA",   "Asia/Amman",       [9, 12, 19, 21], "ar"),
    ("Lebanon",               "LB", "MENA",   "Asia/Beirut",      [9, 12, 19, 21], "ar"),
    ("Iraq",                  "IQ", "MENA",   "Asia/Baghdad",     [9, 12, 19, 21], "ar"),
    ("Yemen",                 "YE", "MENA",   "Asia/Aden",        [9, 12, 19, 21], "ar"),

    # ── Europe — Western European Time (UTC+0/+1) ──────────────────────────────
    ("United Kingdom",        "GB", "Europe", "Europe/London",    [8, 12, 19, 20], "en"),
    ("Ireland",               "IE", "Europe", "Europe/Dublin",    [8, 12, 19, 20], "en"),
    ("Portugal",              "PT", "Europe", "Europe/Lisbon",    [8, 12, 19, 20], "pt"),

    # ── Europe — Central European Time (UTC+1/+2) ─────────────────────────────
    ("France",                "FR", "Europe", "Europe/Paris",     [8, 12, 19, 20], "fr"),
    ("Germany",               "DE", "Europe", "Europe/Berlin",    [8, 12, 19, 20], "de"),
    ("Spain",                 "ES", "Europe", "Europe/Madrid",    [8, 12, 19, 20], "es"),
    ("Italy",                 "IT", "Europe", "Europe/Rome",      [8, 12, 19, 20], "it"),
    ("Netherlands",           "NL", "Europe", "Europe/Amsterdam", [8, 12, 19, 20], "nl"),
    ("Belgium",               "BE", "Europe", "Europe/Brussels",  [8, 12, 19, 20], "fr"),
    ("Switzerland",           "CH", "Europe", "Europe/Zurich",    [8, 12, 19, 20], "de"),
    ("Austria",               "AT", "Europe", "Europe/Vienna",    [8, 12, 19, 20], "de"),
    ("Sweden",                "SE", "Europe", "Europe/Stockholm", [8, 12, 19, 20], "en"),
    ("Norway",                "NO", "Europe", "Europe/Oslo",      [8, 12, 19, 20], "en"),
    ("Denmark",               "DK", "Europe", "Europe/Copenhagen",[8, 12, 19, 20], "en"),
    ("Luxembourg",            "LU", "Europe", "Europe/Luxembourg",[8, 12, 19, 20], "fr"),

    # ── Europe — Eastern European Time (UTC+2/+3) ─────────────────────────────
    ("Poland",                "PL", "Europe", "Europe/Warsaw",    [8, 12, 19, 20], "en"),
    ("Romania",               "RO", "Europe", "Europe/Bucharest", [8, 12, 19, 20], "en"),
    ("Greece",                "GR", "Europe", "Europe/Athens",    [8, 12, 19, 20], "en"),
    ("Turkey",                "TR", "Europe", "Europe/Istanbul",  [8, 12, 19, 20], "tr"),
    ("Czech Republic",        "CZ", "Europe", "Europe/Prague",    [8, 12, 19, 20], "en"),
    ("Hungary",               "HU", "Europe", "Europe/Budapest",  [8, 12, 19, 20], "en"),
    ("Finland",               "FI", "Europe", "Europe/Helsinki",  [8, 12, 19, 20], "en"),
    ("Ukraine",               "UA", "Europe", "Europe/Kiev",      [8, 12, 19, 20], "en"),
    ("Bulgaria",              "BG", "Europe", "Europe/Sofia",     [8, 12, 19, 20], "en"),
    ("Serbia",                "RS", "Europe", "Europe/Belgrade",  [8, 12, 19, 20], "en"),
    ("Croatia",               "HR", "Europe", "Europe/Zagreb",    [8, 12, 19, 20], "en"),
    ("Slovakia",              "SK", "Europe", "Europe/Bratislava",[8, 12, 19, 20], "en"),
    ("Slovenia",              "SI", "Europe", "Europe/Ljubljana", [8, 12, 19, 20], "en"),

    # ── United States — 4 timezones ───────────────────────────────────────────
    ("United States (Eastern)",  "US", "Americas", "America/New_York",    [8, 12, 19, 20], "en"),
    ("United States (Central)",  "US", "Americas", "America/Chicago",     [8, 12, 19, 20], "en"),
    ("United States (Mountain)", "US", "Americas", "America/Denver",      [8, 12, 19, 20], "en"),
    ("United States (Pacific)",  "US", "Americas", "America/Los_Angeles", [8, 12, 19, 20], "en"),

    # ── Canada — 3 timezones ──────────────────────────────────────────────────
    ("Canada (Eastern)",  "CA", "Americas", "America/Toronto",   [8, 12, 19, 20], "en"),
    ("Canada (Central)",  "CA", "Americas", "America/Winnipeg",  [8, 12, 19, 20], "en"),
    ("Canada (Pacific)",  "CA", "Americas", "America/Vancouver", [8, 12, 19, 20], "en"),

    # ── Brazil ────────────────────────────────────────────────────────────────
    ("Brazil",            "BR", "Americas", "America/Sao_Paulo", [8, 12, 19, 20], "pt"),

    # ── South Asia (additional) ───────────────────────────────────────────────
    ("Pakistan",          "PK", "South Asia", "Asia/Karachi",    [8, 12, 19, 21], "ur"),
    ("Bangladesh",        "BD", "South Asia", "Asia/Dhaka",      [8, 12, 19, 21], "en"),
    ("Sri Lanka",         "LK", "South Asia", "Asia/Colombo",    [8, 12, 19, 21], "en"),
    ("Nepal",             "NP", "South Asia", "Asia/Kathmandu",  [8, 12, 19, 21], "en"),

    # ── Southeast Asia ────────────────────────────────────────────────────────
    ("Malaysia",          "MY", "Southeast Asia", "Asia/Kuala_Lumpur", [8, 12, 19, 21], "ms"),
    ("Indonesia",         "ID", "Southeast Asia", "Asia/Jakarta",      [8, 12, 19, 21], "id"),
    ("Singapore",         "SG", "Southeast Asia", "Asia/Singapore",    [8, 12, 19, 21], "en"),
    ("Philippines",       "PH", "Southeast Asia", "Asia/Manila",       [8, 12, 19, 21], "en"),
    ("Thailand",          "TH", "Southeast Asia", "Asia/Bangkok",      [8, 12, 19, 21], "en"),

    # ── East Asia ─────────────────────────────────────────────────────────────
    ("Australia (Sydney)",   "AU", "Oceania", "Australia/Sydney",   [8, 12, 19, 20], "en"),
    ("Australia (Perth)",    "AU", "Oceania", "Australia/Perth",    [8, 12, 19, 20], "en"),
    ("Australia (Brisbane)", "AU", "Oceania", "Australia/Brisbane", [8, 12, 19, 20], "en"),
    ("New Zealand",          "NZ", "Oceania", "Pacific/Auckland",   [8, 12, 19, 20], "en"),

    # ── Africa ────────────────────────────────────────────────────────────────
    ("Nigeria",           "NG", "Africa", "Africa/Lagos",      [8, 12, 19, 21], "en"),
    ("South Africa",      "ZA", "Africa", "Africa/Johannesburg",[8, 12, 19, 20], "en"),
    ("Kenya",             "KE", "Africa", "Africa/Nairobi",    [8, 12, 19, 21], "en"),
    ("Ghana",             "GH", "Africa", "Africa/Accra",      [8, 12, 19, 21], "en"),
    ("Ethiopia",          "ET", "Africa", "Africa/Addis_Ababa",[8, 12, 19, 21], "en"),
    ("Tanzania",          "TZ", "Africa", "Africa/Dar_es_Salaam",[8,12, 19, 21], "en"),
]


# ── Template seed data ─────────────────────────────────────────────────────────
# Format: (name, language_code, title, body, notif_type)

TEMPLATES = [
    # English
    (
        "Evening Match Promo (EN)", "en",
        "Your perfect match is waiting 💕",
        "Someone out there is looking for exactly you. Open Zod and connect tonight.",
        "promotions",
    ),
    (
        "Dating Tips — How to start a great convo (EN)", "en",
        "First message tips 💬",
        "The best openers are specific and genuine. Try asking about something on their profile!",
        "dating_tips",
    ),
    (
        "Morning Motivation (EN)", "en",
        "Start your day with a new connection ☀️",
        "New profiles are waiting for you. Open Zod and see who matched with you overnight.",
        "promotions",
    ),

    # Arabic
    (
        "مساء المباراة (AR)", "ar",
        "شخص ما ينتظرك الآن 💕",
        "اكتشف من أعجب بك اليوم. افتح زود وتواصل مع شخص مميز.",
        "promotions",
    ),
    (
        "نصائح المواعدة (AR)", "ar",
        "كيف تبدأ محادثة رائعة 💬",
        "أفضل الرسائل تكون صادقة وشخصية. جرّب أن تسأل عن شيء في ملفه الشخصي!",
        "dating_tips",
    ),
    (
        "صباح جديد (AR)", "ar",
        "ابدأ يومك بتواصل جديد ☀️",
        "ملفات جديدة تنتظرك. افتح زود واكتشف من تطابق معك الليلة.",
        "promotions",
    ),

    # French
    (
        "Promo soirée (FR)", "fr",
        "Votre match parfait vous attend 💕",
        "Quelqu'un vous cherche ce soir. Ouvrez Zod et connectez-vous maintenant.",
        "promotions",
    ),
    (
        "Conseils rencontres (FR)", "fr",
        "Comment démarrer une super conversation 💬",
        "Les meilleurs messages sont sincères et spécifiques. Posez une question sur leur profil !",
        "dating_tips",
    ),

    # Spanish
    (
        "Promo noche (ES)", "es",
        "Tu pareja perfecta te espera 💕",
        "Alguien está buscando exactamente a alguien como tú. Abre Zod y conéctate esta noche.",
        "promotions",
    ),
    (
        "Consejos de citas (ES)", "es",
        "Cómo iniciar una gran conversación 💬",
        "Los mejores mensajes son específicos y genuinos. ¡Pregunta algo sobre su perfil!",
        "dating_tips",
    ),

    # Portuguese
    (
        "Promo noturna (PT)", "pt",
        "Sua combinação perfeita está esperando 💕",
        "Alguém por aí está procurando exatamente por você. Abra o Zod e conecte-se hoje à noite.",
        "promotions",
    ),
    (
        "Dicas de namoro (PT)", "pt",
        "Como começar uma ótima conversa 💬",
        "As melhores mensagens são específicas e genuínas. Tente perguntar algo sobre o perfil deles!",
        "dating_tips",
    ),

    # Hindi
    (
        "शाम की मैच प्रोमो (HI)", "hi",
        "आपका परफेक्ट मैच इंतज़ार कर रहा है 💕",
        "कोई आपको ढूंढ रहा है। अभी Zod खोलें और आज रात कनेक्ट करें।",
        "promotions",
    ),
    (
        "डेटिंग टिप्स (HI)", "hi",
        "अच्छी बातचीत कैसे शुरू करें 💬",
        "सबसे अच्छे मैसेज ईमानदार और व्यक्तिगत होते हैं। उनकी प्रोफ़ाइल के बारे में कुछ पूछें!",
        "dating_tips",
    ),

    # German
    (
        "Abend-Match-Promo (DE)", "de",
        "Dein perfekter Match wartet 💕",
        "Jemand sucht genau nach dir. Öffne Zod und verbinde dich heute Abend.",
        "promotions",
    ),
    (
        "Dating-Tipps (DE)", "de",
        "So startest du ein tolles Gespräch 💬",
        "Die besten Nachrichten sind spezifisch und aufrichtig. Frag etwas über ihr Profil!",
        "dating_tips",
    ),

    # Italian
    (
        "Promo serale (IT)", "it",
        "Il tuo match perfetto ti aspetta 💕",
        "C'è qualcuno là fuori che cerca esattamente te. Apri Zod e connettiti stasera.",
        "promotions",
    ),

    # Dutch
    (
        "Avond-match promo (NL)", "nl",
        "Jouw perfecte match wacht op je 💕",
        "Iemand is op zoek naar precies jou. Open Zod en maak vanavond een connectie.",
        "promotions",
    ),

    # Turkish
    (
        "Akşam eşleşme promosyonu (TR)", "tr",
        "Mükemmel eşleşmen seni bekliyor 💕",
        "Biri tam olarak seni arıyor. Zod'u aç ve bu gece bağlan.",
        "promotions",
    ),

    # Urdu
    (
        "شام کی میچ پروموشن (UR)", "ur",
        "آپ کا بہترین میچ انتظار کر رہا ہے 💕",
        "کوئی آپ کو تلاش کر رہا ہے۔ ابھی Zod کھولیں اور آج رات کنیکٹ کریں۔",
        "promotions",
    ),

    # Malay
    (
        "Promo malam (MS)", "ms",
        "Pasangan sempurna anda menunggu 💕",
        "Seseorang mencari seseorang seperti anda. Buka Zod dan berhubung malam ini.",
        "promotions",
    ),

    # Indonesian
    (
        "Promo malam (ID)", "id",
        "Pasangan sempurna Anda menunggu 💕",
        "Ada seseorang yang mencari Anda. Buka Zod dan terhubung malam ini.",
        "promotions",
    ),
]


async def seed():
    import json
    async with Session() as db:
        # ── Countries ─────────────────────────────────────────────────────────
        print("Seeding countries...")
        for name, code, region, tz_name, peak_hours, primary_language in COUNTRIES:
            await db.execute(
                text("""
                    INSERT INTO marketing_countries
                        (name, code, region, tz_name, peak_hours, primary_language, is_active)
                    VALUES
                        (:name, :code, :region, :tz_name, CAST(:peak_hours AS jsonb),
                         :primary_language, TRUE)
                    ON CONFLICT (code, tz_name) DO UPDATE SET
                        name             = EXCLUDED.name,
                        region           = EXCLUDED.region,
                        peak_hours       = EXCLUDED.peak_hours,
                        primary_language = EXCLUDED.primary_language,
                        is_active        = EXCLUDED.is_active
                """).bindparams(
                    name=name,
                    code=code,
                    region=region,
                    tz_name=tz_name,
                    peak_hours=json.dumps(peak_hours),
                    primary_language=primary_language,
                )
            )
        await db.commit()
        print(f"  ✓ {len(COUNTRIES)} country-timezone entries upserted")

        # ── Templates — only insert if table is empty ─────────────────────────
        existing = (await db.execute(text("SELECT COUNT(*) FROM marketing_templates"))).scalar()
        if existing == 0:
            print("Seeding templates...")
            for tmpl_name, lang, title, body, notif_type in TEMPLATES:
                await db.execute(
                    text("""
                        INSERT INTO marketing_templates
                            (name, language_code, title, body, notif_type, is_active,
                             created_at, updated_at)
                        VALUES
                            (:name, :lang, :title, :body, :notif_type, TRUE,
                             NOW(), NOW())
                    """).bindparams(
                        name=tmpl_name, lang=lang, title=title,
                        body=body, notif_type=notif_type,
                    )
                )
            await db.commit()
            print(f"  ✓ {len(TEMPLATES)} templates inserted")
        else:
            print(f"  ↩ Templates table already has {existing} rows — skipped")

    await engine.dispose()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(seed())
