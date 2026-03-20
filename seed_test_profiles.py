"""
Seed test profiles:
  • 50 near London  (25 date + 25 work) — original batch
  • 20 near Riyadh  (15 date + 5 work)  — added for Riyadh testing
  • 15 near Western US (date profiles)  — added for US testing

All coords are within 80 km of the respective city centre.
Also creates ~30 mutual likes (matches) between the seeded users.

Run:  python3 seed_test_profiles.py
"""

import asyncio
import json
import math
import random
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

from app.core.config import settings

# ── DB config ──────────────────────────────────────────────────────────────────
# Credentials are loaded from .env via app/core/config.py (pydantic-settings).
DB = {
    "host":     settings.DB_HOST,
    "port":     settings.DB_PORT,
    "database": settings.DB_NAME,
    "user":     settings.DB_USERNAME,
    "password": settings.DB_PASSWORD,
    "ssl":      settings.DB_SSLMODE,
}

# ── Base coords: London ────────────────────────────────────────────────────────
BASE_LAT = 51.5074
BASE_LNG = -0.1278
MAX_RADIUS_KM = 78  # keep all within 80 km

# ── Lookup IDs from the database ──────────────────────────────────────────────
GENDER_IDS        = [223, 224, 225]           # Man, Woman, Non-binary
EDUCATION_IDS     = [5, 6, 7, 8, 9, 10, 11]
LOOKING_FOR_IDS   = [19, 20, 21, 22, 23]
FAMILY_PLANS_IDS  = [24, 25, 26, 27]
HAVE_KIDS_IDS     = [28, 29, 30]
STAR_SIGN_IDS     = list(range(31, 43))       # 31-42
RELIGION_IDS      = list(range(43, 52))       # 43-51
EXERCISE_IDS      = [1, 2, 3, 4]
DRINKING_IDS      = [12, 13, 14]
SMOKING_IDS       = [15, 16, 17, 18]
DIET_IDS          = [286, 287, 288, 289, 290, 291, 292]
INTEREST_IDS      = list(range(227, 250))     # 227-249
LANGUAGE_IDS      = list(range(52, 68))       # 52-67

WORK_COMMITMENT_IDS = [72, 73, 74, 75]
WORK_EQUITY_IDS     = [76, 77, 78, 79, 80]
WORK_INDUSTRY_IDS   = list(range(81, 109))    # 81-108
WORK_SKILL_IDS      = list(range(109, 129))   # 109-128
WORK_MATCHING_GOAL_IDS = [68, 69, 70, 71]

# ── Random photo pools (randomuser.me) ────────────────────────────────────────
MALE_PHOTOS   = [f"https://randomuser.me/api/portraits/men/{i}.jpg"   for i in range(1, 80)]
FEMALE_PHOTOS = [f"https://randomuser.me/api/portraits/women/{i}.jpg" for i in range(1, 80)]

DATE_FIRST_NAMES_M = [
    "James","Oliver","Ethan","Noah","Liam","Lucas","Harry","Charlie","Jack","George",
    "Finn","Ryo","Mateo","Santiago","Ivan","Tobias","Felix","Marcus","Kai","Leon",
    "Nathan","Aaron","Eliot","Blake","Caspian",
]
DATE_FIRST_NAMES_F = [
    "Emma","Olivia","Sophie","Isabella","Mia","Ava","Amelia","Charlotte","Ella","Isla",
    "Freya","Zara","Luna","Nadia","Priya","Aisha","Yuna","Sofia","Valentina","Rosa",
    "Maya","Jade","Aria","Chloe","Leila",
]
WORK_FIRST_NAMES = [
    "Alex","Jordan","Morgan","Riley","Taylor","Drew","Casey","Quinn","Reese","Sage",
    "Skylar","Avery","Parker","Blake","Logan","Jamie","Cameron","Peyton","Rowan","Elliot",
    "Noa","Kai","River","Phoenix","Finley",
]
LAST_NAMES = [
    "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Wilson","Moore",
    "Taylor","Anderson","Thomas","Jackson","White","Harris","Martin","Thompson","Young","Lewis",
    "Walker","Hall","Allen","King","Wright","Scott","Baker","Green","Adams","Nelson",
]

DATE_BIOS = [
    "Coffee lover, adventure seeker, and occasional baker 🍰",
    "Hiking on weekends, coding during the week. Looking for my partner in crime 🏕️",
    "Art galleries in the morning, rooftop bars at night 🎨🥂",
    "Yoga, good books, and strong espresso make me who I am ☕📚",
    "Serial traveller — 42 countries and counting ✈️",
    "Film nerd who can quote any Kubrick film on demand 🎬",
    "Plant-based chef by heart, engineer by trade 🌱",
    "Dance floor or dinner table — I'm equally at home 💃",
    "Marathon runner fuelled by pasta and optimism 🏃‍♀️",
    "Marine biologist with a thing for sushi (ethically sourced!) 🐠",
    "I make music on Saturdays and make mistakes the rest of the week 🎸",
    "Equal parts introvert and adventure junkie — yes, it's possible 🏔️",
    "Obsessed with farmers markets, farmers tans, and bad puns 🌽",
    "Tech by day, jazz by night. Seeking someone who appreciates both 🎷",
    "Wine lover, dog parent, professional overthinker 🐕🍷",
]

WORK_BIOS = [
    "Former FAANG engineer now building AI infra for the next wave of founders.",
    "5 years fintech product @ Revolut. Obsessed with distribution and growth loops.",
    "Sold my last startup to Shopify in 2022. Back at it in climate tech.",
    "Deep learning researcher with 3 published papers. Looking for a GTM co-founder.",
    "Serial entrepreneur (2 exits). Now targeting healthtech disruption.",
    "Head of Product at Stripe. Ready to build something of my own.",
    "Venture-backed founder building future of work infrastructure.",
    "Ex-McKinsey, ex-Google. Thesis: the next $10B company is hiding in logistics.",
    "YC alum looking for a technical co-founder to build in developer tools.",
    "Marketing genius who can 10x user acquisition from day one.",
    "CTO with a knack for hiring world-class engineering teams at early stage.",
    "Angel investor turned founder — combining $2M cheque book with execution.",
    "Legal-turned-operator who understands both compliance and growth.",
    "Data scientist who can turn raw signals into product decisions in days.",
    "Community builder with 50k followers who wants to productise the audience.",
]

PROMPT_POOLS = [
    [{"question": "The way to win me over is…", "answer": "Show up with coffee and zero agenda ☕"},
     {"question": "My ideal Sunday looks like…", "answer": "Farmers market → long walk → nowhere to be 🌿"}],
    [{"question": "Don't be mad if I…", "answer": "Order dessert before checking the menu 🍰"},
     {"question": "Catch flights or feelings?", "answer": "Both. Simultaneously. Efficiency 🛫❤️"}],
    [{"question": "My most controversial opinion…", "answer": "Pineapple on pizza is fine. Fight me 🍍"},
     {"question": "The thing that makes me laugh every time…", "answer": "Dogs wearing sunglasses 😎🐶"}],
    [{"question": "Two truths and a lie…", "answer": "I've climbed Kilimanjaro. I hate avocado. I speak 3 languages 🌍"},
     {"question": "I know the best spot for…", "answer": "Hidden rooftop bars you've never heard of 🌃"}],
    [{"question": "My love language is…", "answer": "Quality time — no phones, just presence 🤝"},
     {"question": "Green flags for me include…", "answer": "People who tip well and remember your order ☕"}],
]

WORK_PROMPT_POOLS = [
    [{"question": "My idea in one line", "answer": "GPT-native ERP — replace 5 SaaS tools with one AI layer."},
     {"question": "The co-founder I'm looking for", "answer": "A sales-obsessed operator who can close enterprise deals from day one."}],
    [{"question": "What I bring to the table", "answer": "Deep domain expertise, 200+ investor relationships, and a relentless GTM engine."},
     {"question": "My biggest learning so far", "answer": "Distribution beats product every single time."}],
    [{"question": "Why now, why me", "answer": "I have the scar tissue from one exit and know every mistake to avoid."},
     {"question": "The problem I'm solving", "answer": "SMBs waste 40% of revenue on fragmented software subscriptions."}],
    [{"question": "My superpower is", "answer": "I cut LLM inference cost by 60% at my last role — I can do it at yours."},
     {"question": "What I look for in a founding team", "answer": "Complementary skills, shared values, and the ability to disagree productively."}],
    [{"question": "The moment I knew this was real", "answer": "When five enterprise customers said they'd pay before we wrote a line of code."},
     {"question": "I'm most energised by", "answer": "Early mornings, hard problems, and zero corporate overhead."}],
]

CITIES_NEAR_LONDON = [
    ("London", "UK"),
    ("London, Canary Wharf", "UK"),
    ("London, Shoreditch", "UK"),
    ("London, Brixton", "UK"),
    ("London, Notting Hill", "UK"),
    ("London, Hackney", "UK"),
    ("London, Islington", "UK"),
    ("London, Camden", "UK"),
    ("Greenwich", "UK"),
    ("Richmond", "UK"),
    ("Kingston upon Thames", "UK"),
    ("Wimbledon", "UK"),
    ("Croydon", "UK"),
    ("Watford", "UK"),
    ("St Albans", "UK"),
    ("Guildford", "UK"),
    ("Reading", "UK"),
    ("Oxford", "UK"),
    ("Cambridge", "UK"),
    ("Luton", "UK"),
    ("Brighton", "UK"),
    ("Sevenoaks", "UK"),
    ("Chelmsford", "UK"),
    ("Hertford", "UK"),
    ("Windsor", "UK"),
]

# ── Riyadh seed data ──────────────────────────────────────────────────────────
RYD_BASE_LAT   = 24.7136
RYD_BASE_LNG   = 46.6753
RYD_MAX_RADIUS = 60   # km

RIYADH_MALE_NAMES = [
    "Fahad", "Khalid", "Abdulaziz", "Sultan", "Turki",
    "Faisal", "Mansour", "Nasser", "Saud", "Omar",
    "Waleed", "Salman", "Hamad", "Majed", "Rayan",
]
RIYADH_FEMALE_NAMES = [
    "Nora", "Layan", "Reem", "Sara", "Dana",
    "Hessa", "Maha", "Noura", "Razan", "Reema",
    "Shahad", "Dalal", "Ghada", "Amal", "Lina",
]
RIYADH_LAST_NAMES = [
    "Al-Rashid", "Al-Otaibi", "Al-Dosari", "Al-Qahtani", "Al-Shehri",
    "Al-Harthi", "Al-Zahrani", "Al-Anzi", "Al-Mutairi", "Al-Ghamdi",
    "Al-Harbi", "Al-Maliki", "Al-Shamrani", "Al-Omari", "Al-Subaie",
]
RIYADH_DATE_BIOS = [
    "Coffee addict and architecture enthusiast exploring Riyadh's hidden gems ☕🕌",
    "Bookworm by day, foodie by night — best shawarma spots are my love language 📚",
    "Gym, good coffee, and great company. Looking for someone who appreciates all three 💪",
    "Pilot who spends layovers discovering new cuisines ✈️🍜",
    "Dentist by profession, amateur photographer at heart 📷",
    "Loves hiking in Al Hada mountains and camping under Saudi stars ⛺🌟",
    "Fashion designer with a passion for blending heritage and modern style 👗",
    "Entrepreneur building the next big thing in KSA's startup scene 🚀",
    "Avid reader and board game collector — looking for my player two 🎲📖",
    "Engineer at NEOM, chasing the future one megaproject at a time 🏗️",
    "Yoga instructor who believes in mindful living and good playlists 🧘‍♀️🎵",
    "Chef with a flair for Saudi and Japanese fusion — will cook for the right person 🍣🥘",
    "Doctor who unwinds with oud music and long desert drives 🎶🏜️",
    "Startup founder, marathon runner, eternal optimist 🏃‍♂️💡",
    "Interior designer obsessed with blending Saudi tradition and Scandinavian minimalism 🏡",
]
# ── Western US seed data ──────────────────────────────────────────────────────
US_BASE_LAT   = 34.0522   # Los Angeles
US_BASE_LNG   = -118.2437
US_MAX_RADIUS = 200  # km — covers LA, San Diego, Santa Barbara, etc.

US_FEMALE_NAMES = [
    "Hailey", "Madison", "Savannah", "Brittany", "Kylie",
    "Brooke", "Tiffany", "Ashley", "Whitney", "Amber",
    "Cassidy", "Shelby", "Kayla", "Sierra", "Kelsey",
    "Brenna", "Paige", "Lacey", "Alexis", "Taylor",
]
US_MALE_NAMES = [
    "Abdul Kumshey", "Cody", "Tyler", "Chase", "Bryce",
    "Garrett", "Trevor", "Blake", "Colton", "Tanner",
    "Dakota", "Jared", "Kyle", "Dustin", "Austin",
]
US_LAST_NAMES = [
    "Mitchell", "Campbell", "Parker", "Evans", "Edwards",
    "Collins", "Stewart", "Morris", "Rogers", "Reed",
    "Cook", "Morgan", "Bell", "Murphy", "Bailey",
    "Rivera", "Cooper", "Richardson", "Cox", "Howard",
]
US_DATE_BIOS = [
    "SoCal native, beach volleyball addict, and avocado toast enthusiast 🏐🥑",
    "Hiking Runyon Canyon every morning before the city wakes up 🌄",
    "Golden hour chaser — camera in one hand, cold brew in the other ☕📸",
    "Surfer by soul, software engineer by day 🏄‍♀️💻",
    "Farmers market Saturday, live music Sunday — that's my whole personality 🎶🌽",
    "Pilates, puppy playdates, and Pacific sunsets 🐾🌅",
    "Plant mom with 37 succulents and counting 🌵",
    "Weekend road tripper — I've done PCH three times and I'm not stopping 🚗🌊",
    "Yoga retreat veteran who still eats In-N-Out after 🧘‍♀️🍔",
    "Film student turned UX designer — I think in storyboards 🎬",
    "Desert hiker, city dreamer. Joshua Tree is my happy place 🪨⛺",
    "Marathoner, taco connoisseur, and aspiring van-lifer 🌮🚐",
    "Barista by morning, pottery student by evening — living my best life 🏺☕",
    "Marine mammal biologist who cries at dolphin documentaries 🐬",
    "Startup life by week, ski trips to Mammoth by weekend ⛷️🚀",
]
US_CITIES = [
    ("Los Angeles", "USA"),
    ("Los Angeles, Silver Lake", "USA"),
    ("Los Angeles, Venice Beach", "USA"),
    ("Los Angeles, Echo Park", "USA"),
    ("Santa Monica", "USA"),
    ("Malibu", "USA"),
    ("Pasadena", "USA"),
    ("Long Beach", "USA"),
    ("San Diego", "USA"),
    ("San Diego, North Park", "USA"),
    ("Irvine", "USA"),
    ("Santa Barbara", "USA"),
    ("San Francisco", "USA"),
    ("Oakland", "USA"),
    ("Palm Springs", "USA"),
]

RIYADH_CITIES = [
    ("Riyadh", "Saudi Arabia"),
    ("Riyadh, Al Olaya", "Saudi Arabia"),
    ("Riyadh, Al Malaz", "Saudi Arabia"),
    ("Riyadh, Al Muruj", "Saudi Arabia"),
    ("Riyadh, Al Sahafa", "Saudi Arabia"),
    ("Riyadh, Al Nakheel", "Saudi Arabia"),
    ("Riyadh, Hittin", "Saudi Arabia"),
    ("Riyadh, Al Yasmin", "Saudi Arabia"),
    ("Al Kharj", "Saudi Arabia"),
    ("Diriyah", "Saudi Arabia"),
]


def random_coords_near(base_lat: float, base_lng: float, max_km: float):
    """Generate a random lat/lng within max_km of the base point."""
    r = random.uniform(0.5, max_km)
    bearing = random.uniform(0, 2 * math.pi)
    delta_lat = math.degrees(r / 6371)
    delta_lng = math.degrees(r / (6371 * math.cos(math.radians(base_lat))))
    lat = base_lat + delta_lat * math.sin(bearing)
    lng = base_lng + delta_lng * math.cos(bearing)
    return round(lat, 6), round(lng, 6)


def random_dob(min_age=21, max_age=38):
    today = date.today()
    days = random.randint(min_age * 365, max_age * 365)
    return today - timedelta(days=days)


def pick(lst, k=1):
    return random.sample(lst, min(k, len(lst)))


def make_date_profile(idx: int):
    is_male = random.random() < 0.5
    first_name = random.choice(DATE_FIRST_NAMES_M if is_male else DATE_FIRST_NAMES_F)
    last_name = random.choice(LAST_NAMES)
    gender_id = random.choice([223] if is_male else [224, 225])
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    num_photos = random.randint(2, 4)
    photos = random.sample(photos_pool, num_photos)

    lat, lng = random_coords_near(BASE_LAT, BASE_LNG, MAX_RADIUS_KM)
    city_name, country = random.choice(CITIES_NEAR_LONDON)
    dob = random_dob(22, 37)

    interests = pick(INTEREST_IDS, random.randint(3, 7))
    languages = pick(LANGUAGE_IDS, random.randint(1, 3))

    lifestyle = {
        "exercise": random.choice(EXERCISE_IDS),
        "drinking": random.choice(DRINKING_IDS),
        "smoking":  random.choice(SMOKING_IDS),
        "diet":     random.choice(DIET_IDS),
    }

    return {
        "id":                 str(uuid.uuid4()),
        "full_name":          f"{first_name} {last_name}",
        "bio":                random.choice(DATE_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.65,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 180)),
        "updated_at":         datetime.now(timezone.utc),
        "date_of_birth":      dob,
        "latitude":           lat,
        "longitude":          lng,
        "city":               city_name,
        "country":            country,
        "address":            f"{city_name}, {country}",
        "height_cm":          random.randint(155, 195),
        "gender_id":          gender_id,
        "education_level_id": random.choice(EDUCATION_IDS),
        "looking_for_id":     random.choice(LOOKING_FOR_IDS),
        "family_plans_id":    random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":       random.choice(HAVE_KIDS_IDS),
        "star_sign_id":       random.choice(STAR_SIGN_IDS),
        "religion_id":        random.choice(RELIGION_IDS),
        "subscription_tier":  random.choices(["free", "pro"], weights=[70, 30])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[35, 65])[0],
        "photos":             json.dumps(photos),
        "interests":          json.dumps([{"id": i} for i in interests]),
        "languages":          json.dumps([{"id": i} for i in languages]),
        "lifestyle":          json.dumps(lifestyle),
        "prompts":            json.dumps(random.choice(PROMPT_POOLS)),
        "purpose":            json.dumps([{"id": random.choice(LOOKING_FOR_IDS)}]),
        "filter_max_distance_km": random.choice([50, 80, None]),
        "filter_age_min":     None,
        "filter_age_max":     None,
        "filter_verified_only": False,
        # mark as date profile (no work fields)
        "_mode": "date",
    }


def make_work_profile(idx: int):
    first_name = random.choice(WORK_FIRST_NAMES)
    last_name  = random.choice(LAST_NAMES)
    is_male = random.random() < 0.5
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    gender_id = random.choice([223] if is_male else [224, 225])
    num_photos = random.randint(1, 3)
    photos = random.sample(photos_pool, num_photos)
    work_photos = [random.choice(MALE_PHOTOS if is_male else FEMALE_PHOTOS)]

    lat, lng = random_coords_near(BASE_LAT, BASE_LNG, MAX_RADIUS_KM)
    city_name, country = random.choice(CITIES_NEAR_LONDON)
    dob = random_dob(24, 42)

    languages = pick(LANGUAGE_IDS, random.randint(1, 2))
    industries = pick(WORK_INDUSTRY_IDS, random.randint(2, 4))
    skills = pick(WORK_SKILL_IDS, random.randint(2, 5))
    matching_goals = pick(WORK_MATCHING_GOAL_IDS, random.randint(1, 3))

    work_experience = [
        {
            "job_title": random.choice(["CTO","Head of Product","Senior Engineer","Product Lead","Founder","VP Sales","ML Engineer","Staff Engineer"]),
            "company": random.choice(["Google","Stripe","Revolut","Meta","Mistral AI","OpenAI","Monzo","Airbnb","Uber","Salesforce","McKinsey","Goldman Sachs","Sequoia","Andreessen Horowitz"]),
            "start_year": random.randint(2014, 2020),
            "end_year": random.randint(2021, 2024),
        },
        {
            "job_title": random.choice(["Engineer","Product Manager","Analyst","Consultant","Research Lead"]),
            "company": random.choice(["Y Combinator","Accenture","Deloitte","Bloomberg","Amazon","Apple","Twitter","LinkedIn","Spotify","Shopify"]),
            "start_year": random.randint(2010, 2013),
            "end_year": random.randint(2014, 2016),
        },
    ]

    return {
        "id":                 str(uuid.uuid4()),
        "full_name":          f"{first_name} {last_name}",
        "bio":                random.choice(WORK_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.70,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 180)),
        "updated_at":         datetime.now(timezone.utc),
        "date_of_birth":      dob,
        "latitude":           lat,
        "longitude":          lng,
        "city":               city_name,
        "country":            country,
        "address":            f"{city_name}, {country}",
        "height_cm":          random.randint(158, 195),
        "gender_id":          gender_id,
        "education_level_id": random.choice(EDUCATION_IDS),
        "looking_for_id":     random.choice(LOOKING_FOR_IDS),
        "family_plans_id":    random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":       random.choice(HAVE_KIDS_IDS),
        "star_sign_id":       random.choice(STAR_SIGN_IDS),
        "religion_id":        random.choice(RELIGION_IDS),
        "subscription_tier":  random.choices(["free", "pro"], weights=[55, 45])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[30, 70])[0],
        "photos":             json.dumps(photos),
        "work_photos":        json.dumps(work_photos),
        "interests":          json.dumps([{"id": i} for i in pick(INTEREST_IDS, 4)]),
        "languages":          json.dumps([{"id": i} for i in languages]),
        "lifestyle":          json.dumps({
            "exercise": random.choice(EXERCISE_IDS),
            "drinking": random.choice(DRINKING_IDS),
            "smoking":  random.choice(SMOKING_IDS),
            "diet":     random.choice(DIET_IDS),
        }),
        "prompts":            json.dumps(random.choice(WORK_PROMPT_POOLS)),
        "work_prompts":       json.dumps(random.choice(WORK_PROMPT_POOLS)),
        "work_matching_goals":     json.dumps([{"id": i} for i in matching_goals]),
        "work_commitment_level_id": random.choice(WORK_COMMITMENT_IDS),
        "work_equity_split_id":     random.choice(WORK_EQUITY_IDS),
        "work_industries":    json.dumps([{"id": i} for i in industries]),
        "work_skills":        json.dumps([{"id": i} for i in skills]),
        "work_are_you_hiring": random.random() < 0.35,
        "work_experience":    json.dumps(work_experience),
        "filter_max_distance_km": random.choice([50, 80, None]),
        "filter_age_min":     None,
        "filter_age_max":     None,
        "filter_verified_only": False,
        "_mode": "work",
    }


def make_riyadh_date_profile(idx: int):
    is_male = random.random() < 0.5
    first_name = random.choice(RIYADH_MALE_NAMES if is_male else RIYADH_FEMALE_NAMES)
    last_name = random.choice(RIYADH_LAST_NAMES)
    gender_id = random.choice([223] if is_male else [224, 225])
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    photos = random.sample(photos_pool, random.randint(2, 4))

    lat, lng = random_coords_near(RYD_BASE_LAT, RYD_BASE_LNG, RYD_MAX_RADIUS)
    city_name, country = random.choice(RIYADH_CITIES)
    dob = random_dob(22, 37)

    interests = pick(INTEREST_IDS, random.randint(3, 7))
    languages = pick(LANGUAGE_IDS, random.randint(1, 3))
    lifestyle = {
        "exercise": random.choice(EXERCISE_IDS),
        "drinking": random.choice(DRINKING_IDS),
        "smoking":  random.choice(SMOKING_IDS),
        "diet":     random.choice(DIET_IDS),
    }

    return {
        "id":                 str(uuid.uuid4()),
        "full_name":          f"{first_name} {last_name}",
        "bio":                random.choice(RIYADH_DATE_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.65,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 120)),
        "updated_at":         datetime.now(timezone.utc),
        "date_of_birth":      dob,
        "latitude":           lat,
        "longitude":          lng,
        "city":               city_name,
        "country":            country,
        "address":            f"{city_name}, {country}",
        "height_cm":          random.randint(155, 195),
        "gender_id":          gender_id,
        "education_level_id": random.choice(EDUCATION_IDS),
        "looking_for_id":     random.choice(LOOKING_FOR_IDS),
        "family_plans_id":    random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":       random.choice(HAVE_KIDS_IDS),
        "star_sign_id":       random.choice(STAR_SIGN_IDS),
        "religion_id":        random.choice(RELIGION_IDS),
        "subscription_tier":  random.choices(["free", "pro"], weights=[70, 30])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[35, 65])[0],
        "photos":             json.dumps(photos),
        "interests":          json.dumps([{"id": i} for i in interests]),
        "languages":          json.dumps([{"id": i} for i in languages]),
        "lifestyle":          json.dumps(lifestyle),
        "prompts":            json.dumps(random.choice(PROMPT_POOLS)),
        "purpose":            json.dumps([{"id": random.choice(LOOKING_FOR_IDS)}]),
        "filter_max_distance_km": None,
        "filter_age_min":     None,
        "filter_age_max":     None,
        "filter_verified_only": False,
        "_mode": "date",
    }


def make_riyadh_work_profile(idx: int):
    first_name = random.choice(WORK_FIRST_NAMES)
    last_name  = random.choice(RIYADH_LAST_NAMES)
    is_male = random.random() < 0.5
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    gender_id = random.choice([223] if is_male else [224, 225])
    photos = random.sample(photos_pool, random.randint(1, 3))

    lat, lng = random_coords_near(RYD_BASE_LAT, RYD_BASE_LNG, RYD_MAX_RADIUS)
    city_name, country = random.choice(RIYADH_CITIES)
    dob = random_dob(24, 42)

    languages = pick(LANGUAGE_IDS, random.randint(1, 2))
    industries = pick(WORK_INDUSTRY_IDS, random.randint(2, 4))
    skills = pick(WORK_SKILL_IDS, random.randint(2, 5))
    matching_goals = pick(WORK_MATCHING_GOAL_IDS, random.randint(1, 3))

    work_experience = [
        {
            "job_title": random.choice(["CTO", "Head of Product", "Senior Engineer", "Founder", "VP Growth"]),
            "company": random.choice(["STC", "Aramco Digital", "Noon", "Jahez", "Careem", "stc pay", "Lean Technologies", "Tamara", "Salla", "Foodics"]),
            "start_year": random.randint(2015, 2020),
            "end_year": random.randint(2021, 2024),
        },
    ]

    return {
        "id":                 str(uuid.uuid4()),
        "full_name":          f"{first_name} {last_name}",
        "bio":                random.choice(WORK_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.70,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 120)),
        "updated_at":         datetime.now(timezone.utc),
        "date_of_birth":      dob,
        "latitude":           lat,
        "longitude":          lng,
        "city":               city_name,
        "country":            country,
        "address":            f"{city_name}, {country}",
        "height_cm":          random.randint(158, 195),
        "gender_id":          gender_id,
        "education_level_id": random.choice(EDUCATION_IDS),
        "looking_for_id":     random.choice(LOOKING_FOR_IDS),
        "family_plans_id":    random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":       random.choice(HAVE_KIDS_IDS),
        "star_sign_id":       random.choice(STAR_SIGN_IDS),
        "religion_id":        random.choice(RELIGION_IDS),
        "subscription_tier":  random.choices(["free", "pro"], weights=[55, 45])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[30, 70])[0],
        "photos":             json.dumps(photos),
        "work_photos":        json.dumps([random.choice(photos_pool)]),
        "interests":          json.dumps([{"id": i} for i in pick(INTEREST_IDS, 4)]),
        "languages":          json.dumps([{"id": i} for i in languages]),
        "lifestyle":          json.dumps({
            "exercise": random.choice(EXERCISE_IDS),
            "drinking": random.choice(DRINKING_IDS),
            "smoking":  random.choice(SMOKING_IDS),
            "diet":     random.choice(DIET_IDS),
        }),
        "prompts":            json.dumps(random.choice(WORK_PROMPT_POOLS)),
        "work_prompts":       json.dumps(random.choice(WORK_PROMPT_POOLS)),
        "work_matching_goals":     json.dumps([{"id": i} for i in matching_goals]),
        "work_commitment_level_id": random.choice(WORK_COMMITMENT_IDS),
        "work_equity_split_id":     random.choice(WORK_EQUITY_IDS),
        "work_industries":    json.dumps([{"id": i} for i in industries]),
        "work_skills":        json.dumps([{"id": i} for i in skills]),
        "work_are_you_hiring": random.random() < 0.40,
        "work_experience":    json.dumps(work_experience),
        "filter_max_distance_km": None,
        "filter_age_min":     None,
        "filter_age_max":     None,
        "filter_verified_only": False,
        "_mode": "work",
    }


def make_us_date_profile(idx: int):
    # First profile is always Abdul Kumshey (male); rest alternate female/male
    if idx == 0:
        first_name, last_name = "Abdul", "Kumshey"
        is_male = True
    else:
        is_male = idx % 3 != 0  # ~2/3 female for variety
        first_name = random.choice(US_MALE_NAMES if is_male else US_FEMALE_NAMES)
        last_name = random.choice(US_LAST_NAMES)

    gender_id = random.choice([223] if is_male else [224, 225])
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    photos = random.sample(photos_pool, random.randint(2, 4))

    lat, lng = random_coords_near(US_BASE_LAT, US_BASE_LNG, US_MAX_RADIUS)
    city_name, country = random.choice(US_CITIES)
    dob = random_dob(21, 35)

    interests = pick(INTEREST_IDS, random.randint(3, 7))
    languages = pick(LANGUAGE_IDS, random.randint(1, 2))
    lifestyle = {
        "exercise": random.choice(EXERCISE_IDS),
        "drinking": random.choice(DRINKING_IDS),
        "smoking":  random.choice(SMOKING_IDS),
        "diet":     random.choice(DIET_IDS),
    }

    return {
        "id":                 str(uuid.uuid4()),
        "full_name":          f"{first_name} {last_name}",
        "bio":                random.choice(US_DATE_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.70,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 90)),
        "updated_at":         datetime.now(timezone.utc),
        "date_of_birth":      dob,
        "latitude":           lat,
        "longitude":          lng,
        "city":               city_name,
        "country":            country,
        "address":            f"{city_name}, {country}",
        "height_cm":          random.randint(155, 192),
        "gender_id":          gender_id,
        "education_level_id": random.choice(EDUCATION_IDS),
        "looking_for_id":     random.choice(LOOKING_FOR_IDS),
        "family_plans_id":    random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":       random.choice(HAVE_KIDS_IDS),
        "star_sign_id":       random.choice(STAR_SIGN_IDS),
        "religion_id":        random.choice(RELIGION_IDS),
        "subscription_tier":  random.choices(["free", "pro"], weights=[65, 35])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[30, 70])[0],
        "photos":             json.dumps(photos),
        "interests":          json.dumps([{"id": i} for i in interests]),
        "languages":          json.dumps([{"id": i} for i in languages]),
        "lifestyle":          json.dumps(lifestyle),
        "prompts":            json.dumps(random.choice(PROMPT_POOLS)),
        "purpose":            json.dumps([{"id": random.choice(LOOKING_FOR_IDS)}]),
        "filter_max_distance_km": random.choice([50, 80, None]),
        "filter_age_min":     None,
        "filter_age_max":     None,
        "filter_verified_only": False,
        "_mode": "date",
    }


async def seed():
    print("Connecting to database…")
    conn = await asyncpg.connect(**DB)

    # ── Check if likes/matches table exists ───────────────────────────────────
    has_likes = await conn.fetchval(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'likes')"
    )
    has_matches = await conn.fetchval(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'matches')"
    )
    print(f"likes table: {has_likes}, matches table: {has_matches}")

    # ── Generate profiles ─────────────────────────────────────────────────────
    random.seed(42)
    date_profiles  = [make_date_profile(i) for i in range(25)]
    work_profiles  = [make_work_profile(i) for i in range(25)]
    ryd_date       = [make_riyadh_date_profile(i) for i in range(15)]
    ryd_work       = [make_riyadh_work_profile(i) for i in range(5)]
    us_date        = [make_us_date_profile(i) for i in range(15)]
    all_profiles   = date_profiles + work_profiles + ryd_date + ryd_work + us_date

    print(f"Inserting {len(all_profiles)} profiles  "
          f"(25 London date + 25 London work + 15 Riyadh date + 5 Riyadh work + 15 Western US date)…")

    insert_sql = """
    INSERT INTO users (
        id, full_name, bio, is_active, is_verified, is_onboarded,
        created_at, updated_at, date_of_birth,
        latitude, longitude, city, country, address,
        height_cm, gender_id, education_level_id, looking_for_id,
        family_plans_id, have_kids_id, star_sign_id, religion_id,
        subscription_tier, verification_status,
        photos, interests, languages, lifestyle, prompts, purpose,
        work_photos, work_prompts, work_matching_goals,
        work_commitment_level_id, work_equity_split_id,
        work_industries, work_skills, work_are_you_hiring, work_experience,
        filter_max_distance_km, filter_age_min, filter_age_max, filter_verified_only
    ) VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,
        $10,$11,$12,$13,$14,$15,$16,$17,$18,
        $19,$20,$21,$22,$23,$24,
        $25::jsonb,$26::jsonb,$27::jsonb,$28::jsonb,$29::jsonb,$30::jsonb,
        $31::jsonb,$32::jsonb,$33::jsonb,
        $34,$35,
        $36::jsonb,$37::jsonb,$38,$39::jsonb,
        $40,$41,$42,$43
    )
    ON CONFLICT DO NOTHING
    """

    inserted = 0
    for p in all_profiles:
        try:
            await conn.execute(
                insert_sql,
                p["id"], p["full_name"], p["bio"], p["is_active"], p["is_verified"],
                p["is_onboarded"], p["created_at"], p["updated_at"], p["date_of_birth"],
                p["latitude"], p["longitude"], p["city"], p["country"], p["address"],
                p["height_cm"], p["gender_id"], p["education_level_id"], p["looking_for_id"],
                p["family_plans_id"], p["have_kids_id"], p["star_sign_id"], p["religion_id"],
                p["subscription_tier"], p["verification_status"],
                p["photos"], p["interests"], p["languages"], p["lifestyle"], p["prompts"],
                p.get("purpose", "null"),
                p.get("work_photos") or "null",
                p.get("work_prompts") or "null",
                p.get("work_matching_goals") or "null",
                p.get("work_commitment_level_id"),
                p.get("work_equity_split_id"),
                p.get("work_industries") or "null",
                p.get("work_skills") or "null",
                p.get("work_are_you_hiring"),
                p.get("work_experience") or "null",
                p.get("filter_max_distance_km"),
                p.get("filter_age_min"),
                p.get("filter_age_max"),
                p.get("filter_verified_only", False),
            )
            inserted += 1
        except Exception as e:
            print(f"  ⚠  Failed to insert {p['full_name']}: {e}")

    print(f"✅ Inserted {inserted}/{len(all_profiles)} profiles")

    # ── Seed likes/matches if the tables exist ────────────────────────────────
    all_ids = [p["id"] for p in all_profiles]

    if has_likes:
        print("\nSeeding likes table (mutual = match)…")
        # Create 60 like pairs, ~30 will be mutual (→ match)
        pairs = set()
        while len(pairs) < 60:
            a, b = random.sample(all_ids, 2)
            if (a, b) not in pairs and (b, a) not in pairs:
                pairs.add((a, b))

        like_pairs = list(pairs)
        # First 30 → also add reverse to create mutual likes
        mutual_pairs = like_pairs[:30]
        for a, b in mutual_pairs:
            like_pairs.append((b, a))

        likes_inserted = 0
        for liker_id, liked_id in like_pairs:
            try:
                await conn.execute(
                    """INSERT INTO likes (liker_id, liked_id, created_at)
                       VALUES ($1::uuid, $2::uuid, $3)
                       ON CONFLICT DO NOTHING""",
                    liker_id, liked_id,
                    datetime.now(timezone.utc) - timedelta(days=random.randint(0, 60)),
                )
                likes_inserted += 1
            except Exception as e:
                print(f"  ⚠  like insert failed: {e}")
        print(f"✅ Inserted {likes_inserted} likes ({len(mutual_pairs)} mutual pairs)")

    if has_matches:
        print("\nSeeding matches table…")
        matches_inserted = 0
        for a, b in (mutual_pairs if has_likes else []):
            # Ensure stable ordering to avoid duplicate (a,b) vs (b,a)
            u1, u2 = (a, b) if a < b else (b, a)
            try:
                await conn.execute(
                    """INSERT INTO matches (user1_id, user2_id, matched_at)
                       VALUES ($1::uuid, $2::uuid, $3)
                       ON CONFLICT DO NOTHING""",
                    u1, u2,
                    datetime.now(timezone.utc) - timedelta(days=random.randint(0, 30)),
                )
                matches_inserted += 1
            except Exception as e:
                # Table may use different column names — try alternate schema
                try:
                    await conn.execute(
                        """INSERT INTO matches (user_id_1, user_id_2, created_at)
                           VALUES ($1::uuid, $2::uuid, $3)
                           ON CONFLICT DO NOTHING""",
                        u1, u2,
                        datetime.now(timezone.utc) - timedelta(days=random.randint(0, 30)),
                    )
                    matches_inserted += 1
                except Exception as e2:
                    print(f"  ⚠  match insert failed: {e2}")
        print(f"✅ Inserted {matches_inserted} matches")
    else:
        print("\nℹ  No 'matches' table found — skipping (matches system not yet implemented)")

    await conn.close()
    print("\n🎉 Seed complete!")
    print(f"   • 25 date profiles  (London)")
    print(f"   • 25 work profiles  (London)")
    print(f"   • 15 date profiles  (Riyadh)")
    print(f"   • 5  work profiles  (Riyadh)")
    print(f"   • 15 date profiles  (Western US — incl. Abdul Kumshey + western female names)")
    print(f"   • ~30 mutual likes seeded (where likes table exists)")


if __name__ == "__main__":
    asyncio.run(seed())
