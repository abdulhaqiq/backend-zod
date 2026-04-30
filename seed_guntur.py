"""
Seed female profiles near Guntur, Andhra Pradesh and send
like-notifications to a target user (Abdul Kumshey).

Run:  python3 seed_guntur.py
"""

import asyncio
import json
import math
import random
import ssl
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

# ── DB config ──────────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DB_HOST     = os.environ.get("DB_HOST", "db-postgresql-nyc3-22944-do-user-23814271-0.g.db.ondigitalocean.com")
DB_PORT     = int(os.environ.get("DB_PORT", "25060"))
DB_NAME     = os.environ.get("DB_NAME", "defaultdb")
DB_USER     = os.environ.get("DB_USERNAME", "doadmin")
DB_PASS     = os.environ.get("DB_PASSWORD", "")

# ── Target user (Abdul Kumshey — will receive notifications) ──────────────────
TARGET_USER_ID   = "8673f42f-b546-406b-bc92-b8369f078587"
TARGET_PUSH_TOKEN = "ExponentPushToken[rMds61H1AJVVhJZUlTUvl_]"

# ── Guntur, Andhra Pradesh ────────────────────────────────────────────────────
GNT_BASE_LAT   = 16.3067
GNT_BASE_LNG   = 80.4365
GNT_MAX_RADIUS = 30  # km — covers Guntur, Vijayawada corridor

GUNTUR_FEMALE_NAMES = [
    "Priya", "Divya", "Lakshmi", "Sravani", "Mounika",
    "Haritha", "Swetha", "Manasa", "Kavitha", "Pooja",
    "Anusha", "Ranjitha", "Sirisha", "Bhavana", "Deepika",
]
GUNTUR_LAST_NAMES = [
    "Reddy", "Rao", "Sharma", "Devi", "Nair",
    "Pillai", "Naidu", "Chowdary", "Kumari", "Varma",
    "Srinivas", "Murthy", "Prasad", "Babu", "Krishna",
]
GUNTUR_CITIES = [
    ("Guntur", "India"),
    ("Guntur, Brodipet", "India"),
    ("Guntur, Arundelpet", "India"),
    ("Guntur, Kothapet", "India"),
    ("Vijayawada", "India"),
    ("Tenali", "India"),
    ("Narasaraopet", "India"),
    ("Mangalagiri", "India"),
]
FEMALE_PHOTOS = [f"https://randomuser.me/api/portraits/women/{i}.jpg" for i in range(1, 80)]

GUNTUR_BIOS = [
    "Pharmacist at a hospital in Guntur. I love cooking traditional Andhra food — especially pesarattu on weekends. I'm straightforward and value honesty above everything.",
    "Software engineer working remotely. Grew up in Guntur, came back after three years in Hyderabad. Happy to be closer to family. Looking for someone serious and genuine.",
    "I'm a lecturer at a degree college here. I read a lot, mostly Telugu literature and some English fiction. I believe a good conversation is underrated.",
    "Doctor doing residency at a Guntur government hospital. Long hours teach you exactly what matters in life. I want someone who shows up.",
    "Fashion designer running a small boutique in Brodipet. I'm passionate about handloom fabrics and Kalamkari. Creative and a little stubborn.",
    "MBA graduate working in my family's business. I'm practical, family-oriented, and I believe in building something together, not just dating for the sake of it.",
    "Civil engineer working on a road project near Narasaraopet. I hike in the Nallamala hills when I get weekends. Simple life, big dreams.",
    "Dental surgeon at a private clinic. I'm calm, organized, and a very good listener. Also make the best tomato rice you've ever had.",
    "Teacher at a CBSE school. I spend my weekends at the Krishna riverfront and my evenings reading. Looking for someone with depth, not just surface charm.",
    "Nutritionist and fitness coach. I believe health is wealth — literally. Based in Vijayawada, often in Guntur. Open-minded and direct.",
]

# ── Lookup IDs (same as seed_test_profiles) ───────────────────────────────────
GENDER_FEMALE_ID  = 224
EDUCATION_IDS     = [5, 6, 7, 8, 9, 10, 11]
LOOKING_FOR_IDS   = [19, 20, 21, 22, 23]
FAMILY_PLANS_IDS  = [24, 25, 26, 27]
HAVE_KIDS_IDS     = [28, 29, 30]
STAR_SIGN_IDS     = list(range(31, 43))
RELIGION_IDS      = list(range(43, 52))
EXERCISE_IDS      = [1, 2, 3, 4]
DRINKING_IDS      = [12, 13, 14]
SMOKING_IDS       = [15, 16, 17, 18]
DIET_IDS          = [286, 287, 288, 289, 290, 291, 292]
INTEREST_IDS      = list(range(227, 250))
LANGUAGE_IDS      = list(range(52, 68))

PROMPT_POOLS = [
    [{"question": "The way to win me over is…", "answer": "Be honest and consistent. That's it."},
     {"question": "My ideal Sunday looks like…", "answer": "Filter coffee, a good book, and a long walk by the Krishna river."}],
    [{"question": "A non-negotiable for me is…", "answer": "Family values. Everything else we can figure out."},
     {"question": "I'm looking for someone who…", "answer": "Knows what he wants and isn't afraid to say it."}],
    [{"question": "My friends describe me as…", "answer": "Dependable, a little too honest, and great at cooking."},
     {"question": "On weeknights you'll find me…", "answer": "Winding down with Telugu music or helping amma in the kitchen."}],
    [{"question": "Something most people don't know about me…", "answer": "I can eat three plates of gongura pachadi without stopping."},
     {"question": "What I'm actually looking for…", "answer": "A real partner — someone to build a life with, not just date."}],
    [{"question": "Two truths and a lie…", "answer": "I've been to 10 states. I hate coconut chutney. I wake up at 5am every day."},
     {"question": "I go too far when it comes to…", "answer": "Organising. My wardrobe has labels."}],
]


def random_coords_near(base_lat, base_lng, max_km):
    r = random.uniform(1, max_km)
    bearing = random.uniform(0, 2 * math.pi)
    delta_lat = math.degrees(r / 6371)
    delta_lng = math.degrees(r / (6371 * math.cos(math.radians(base_lat))))
    lat = base_lat + delta_lat * math.sin(bearing)
    lng = base_lng + delta_lng * math.cos(bearing)
    return round(lat, 6), round(lng, 6)


def random_dob(min_age=22, max_age=32):
    today = date.today()
    days = random.randint(min_age * 365, max_age * 365)
    return today - timedelta(days=days)


def random_phone_number():
    """Generate a random Indian phone number"""
    # Indian phone numbers: +91 followed by 10 digits (starting with 6-9)
    first_digit = random.choice(['6', '7', '8', '9'])
    remaining_digits = ''.join([str(random.randint(0, 9)) for _ in range(9)])
    return f"+91{first_digit}{remaining_digits}"


def make_guntur_female(idx: int):
    first_name = random.choice(GUNTUR_FEMALE_NAMES)
    last_name  = random.choice(GUNTUR_LAST_NAMES)
    photos     = random.sample(FEMALE_PHOTOS, random.randint(2, 4))
    lat, lng   = random_coords_near(GNT_BASE_LAT, GNT_BASE_LNG, GNT_MAX_RADIUS)
    city, country = random.choice(GUNTUR_CITIES)
    dob        = random_dob(22, 31)
    interests  = random.sample(INTEREST_IDS, random.randint(3, 6))
    languages  = random.sample(LANGUAGE_IDS, random.randint(1, 3))
    lifestyle  = {
        "exercise": random.choice(EXERCISE_IDS),
        "drinking": random.choice(DRINKING_IDS),
        "smoking":  random.choice(SMOKING_IDS),
        "diet":     random.choice(DIET_IDS),
    }
    phone = random_phone_number()
    return {
        "id":                  str(uuid.uuid4()),
        "full_name":           f"{first_name} {last_name}",
        "phone":               phone,
        "bio":                 random.choice(GUNTUR_BIOS),
        "is_active":           True,
        "is_verified":         random.random() < 0.7,
        "is_onboarded":        True,
        "created_at":          datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 120)),
        "updated_at":          datetime.now(timezone.utc),
        "date_of_birth":       dob,
        "latitude":            lat,
        "longitude":           lng,
        "city":                city,
        "country":             country,
        "address":             f"{city}, {country}",
        "height_cm":           random.randint(152, 170),
        "gender_id":           GENDER_FEMALE_ID,
        "education_level_id":  random.choice(EDUCATION_IDS),
        "looking_for_id":      random.choice(LOOKING_FOR_IDS),
        "family_plans_id":     random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":        random.choice(HAVE_KIDS_IDS),
        "star_sign_id":        random.choice(STAR_SIGN_IDS),
        "religion_id":         random.choice(RELIGION_IDS),
        "subscription_tier":   random.choices(["free", "pro"], weights=[60, 40])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[30, 70])[0],
        "photos":              json.dumps(photos),
        "interests":           json.dumps([{"id": i} for i in interests]),
        "languages":           json.dumps([{"id": i} for i in languages]),
        "lifestyle":           json.dumps(lifestyle),
        "prompts":             json.dumps(random.choice(PROMPT_POOLS)),
        "purpose":             json.dumps([{"id": random.choice(LOOKING_FOR_IDS)}]),
    }


async def send_expo_push(token: str, title: str, body: str, data: dict):
    import urllib.request
    payload = json.dumps({
        "to":        token,
        "title":     title,
        "body":      body,
        "sound":     "default",
        "priority":  "high",
        "channelId": "activity",
        "data":      data,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://exp.host/--/api/v2/push/send",
        data=payload,
        headers={
            "Content-Type":    "application/json",
            "Accept":          "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        status = result.get("data", {}).get("status", "?")
        print(f"   push → {status}")
    except Exception as e:
        print(f"   push error: {e}")


async def seed():
    print("Connecting to database…")
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASS, ssl=ssl_ctx,
    )

    random.seed(None)
    profiles = [make_guntur_female(i) for i in range(20)]

    # ── Check tables ──────────────────────────────────────────────────────────
    has_likes = await conn.fetchval(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='likes')"
    )

    insert_sql = """
    INSERT INTO users (
        id, full_name, phone, bio, is_active, is_verified, is_onboarded,
        created_at, updated_at, date_of_birth,
        latitude, longitude, city, country, address,
        height_cm, gender_id, education_level_id, looking_for_id,
        family_plans_id, have_kids_id, star_sign_id, religion_id,
        subscription_tier, verification_status,
        photos, interests, languages, lifestyle, prompts, purpose,
        work_photos, work_prompts, work_matching_goals,
        work_commitment_level_id, work_equity_split_id,
        work_industries, work_skills, work_are_you_hiring, work_experience,
        work_mode_enabled,
        filter_max_distance_km, filter_age_min, filter_age_max, filter_verified_only
    ) VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
        $11,$12,$13,$14,$15,$16,$17,$18,$19,
        $20,$21,$22,$23,$24,$25,
        $26::jsonb,$27::jsonb,$28::jsonb,$29::jsonb,$30::jsonb,$31::jsonb,
        NULL,NULL,NULL,NULL,NULL,NULL,NULL,false,NULL,false,
        NULL,NULL,NULL,false
    )
    ON CONFLICT DO NOTHING
    """

    inserted_ids = []
    print(f"\nInserting {len(profiles)} female profiles near Guntur…")
    for p in profiles:
        try:
            await conn.execute(
                insert_sql,
                p["id"], p["full_name"], p["phone"], p["bio"], p["is_active"], p["is_verified"],
                p["is_onboarded"], p["created_at"], p["updated_at"], p["date_of_birth"],
                p["latitude"], p["longitude"], p["city"], p["country"], p["address"],
                p["height_cm"], p["gender_id"], p["education_level_id"], p["looking_for_id"],
                p["family_plans_id"], p["have_kids_id"], p["star_sign_id"], p["religion_id"],
                p["subscription_tier"], p["verification_status"],
                p["photos"], p["interests"], p["languages"], p["lifestyle"],
                p["prompts"], p["purpose"],
            )
            inserted_ids.append(p["id"])
            print(f"  ✅ {p['full_name']} ({p['city']}) - {p['phone']}")
        except Exception as e:
            print(f"  ⚠  Failed {p['full_name']}: {e}")

    print(f"\n✅ Inserted {len(inserted_ids)} profiles")

    # ── Seed likes + swipes → target user ────────────────────────────────────
    # Both tables must be populated so the backend match-check (which joins
    # swipes OR likes) correctly detects a mutual like when you swipe right.
    if has_likes and inserted_ids:
        print(f"\nSeeding likes + swipes from all {len(inserted_ids)} profiles → {TARGET_USER_ID}…")
        for liker_id in inserted_ids:
            try:
                await conn.execute(
                    """INSERT INTO likes (liker_id, liked_id, created_at)
                       VALUES ($1::uuid, $2::uuid, $3)
                       ON CONFLICT DO NOTHING""",
                    liker_id, TARGET_USER_ID,
                    datetime.now(timezone.utc),
                )
            except Exception as e:
                print(f"  ⚠  like insert failed: {e}")
            try:
                await conn.execute(
                    """INSERT INTO swipes (swiper_id, swiped_id, direction, mode, created_at)
                       VALUES ($1::uuid, $2::uuid, 'right', 'date', $3)
                       ON CONFLICT (swiper_id, swiped_id, mode) DO NOTHING""",
                    liker_id, TARGET_USER_ID,
                    datetime.now(timezone.utc),
                )
            except Exception as e:
                print(f"  ⚠  swipe insert failed: {e}")
        print(f"✅ Liked + swiped right by all {len(inserted_ids)} profiles")

    # ── Send push notifications ───────────────────────────────────────────────
    print(f"\nSending {len(inserted_ids)} push notifications → {TARGET_PUSH_TOKEN[:40]}…")
    for pid in inserted_ids:
        p = next(x for x in profiles if x["id"] == pid)
        first = p["full_name"].split()[0]
        await send_expo_push(
            TARGET_PUSH_TOKEN,
            title="Someone liked you 💚",
            body=f"{first} from {p['city']} liked your profile!",
            data={"type": "liked_you", "liker_id": pid},
        )
        await asyncio.sleep(0.3)  # gentle rate-limiting

    await conn.close()
    print("\n🎉 Done! Check your phone for notifications.")
    print(f"   {len(inserted_ids)} female profiles seeded in Guntur area")
    print(f"   {len(inserted_ids)} likes sent to your account")
    print(f"   {len(inserted_ids)} push notifications fired")


if __name__ == "__main__":
    asyncio.run(seed())
