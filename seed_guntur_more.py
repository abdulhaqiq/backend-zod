"""Seed 12 more female profiles near Guntur (≤35 km) that like ak@ailoo.co."""
import asyncio, json, math, random, ssl, uuid, os
from datetime import date, datetime, timedelta, timezone
import asyncpg

DB_HOST = os.getenv("DB_HOST", "db-postgresql-nyc3-22944-do-user-23814271-0.g.db.ondigitalocean.com")
DB_PORT = int(os.getenv("DB_PORT", "25060"))
DB_NAME = os.getenv("DB_NAME", "defaultdb")
DB_USER = os.getenv("DB_USER", "doadmin")
DB_PASS = os.getenv("DB_PASS", "")

TARGET_USER_ID    = "8673f42f-b546-406b-bc92-b8369f078587"
TARGET_PUSH_TOKEN = "ExponentPushToken[rMds61H1AJVVhJZUlTUvl_]"

GNT_BASE_LAT = 16.3067; GNT_BASE_LNG = 80.4365; MAX_RADIUS = 35

FIRST_NAMES = ["Yamini","Nandini","Chaitra","Keerthi","Sowmya","Madhuri","Varalakshmi",
               "Sneha","Pavani","Lavanya","Archana","Sunitha","Meghana","Hima","Jyothi",
               "Deepthi","Roshni","Kalyani","Aarushi","Tejaswi"]
LAST_NAMES  = ["Reddy","Rao","Sharma","Naidu","Chowdary","Pillai","Varma","Nair",
               "Srinivas","Murthy","Prasad","Babu","Krishna","Devi","Kumari"]
CITIES = [
    ("Guntur","India"), ("Guntur, Brodipet","India"), ("Guntur, Arundelpet","India"),
    ("Guntur, Kothapet","India"), ("Vijayawada","India"), ("Tenali","India"),
    ("Narasaraopet","India"), ("Mangalagiri","India"), ("Piduguralla","India"),
    ("Sattenapalle","India"), ("Bapatla","India"), ("Ongole","India"),
]
PHOTOS = [f"https://randomuser.me/api/portraits/women/{i}.jpg" for i in range(1, 99)]
BIOS = [
    "Physiotherapist in Guntur. I enjoy classical dance and weekend cooking experiments.",
    "Software developer in Vijayawada. I love Telugu novels and making filter coffee.",
    "Pharmacist at a government hospital. Simple life, big dreams. Looking for genuine.",
    "MBA student at Acharya Nagarjuna University. Family-oriented and very direct.",
    "Primary school teacher in Tenali. I believe kindness matters more than anything.",
    "Civil engineer on an irrigation project near Narasaraopet. Love trekking and sunrises.",
    "Nutritionist with a small clinic in Guntur. Health-conscious and straight-talking.",
    "Fashion designer with a boutique in Brodipet. Passionate about Kalamkari handloom.",
    "Dental surgeon at a private clinic. Calm, organized, great at crossword puzzles.",
    "Banking professional at SBI Guntur. Practical, family-first, loves cooking.",
    "Data analyst working remotely from Guntur. Reads a lot, overthinks a little.",
    "Doctor doing residency at Guntur Medical College. Tired but genuinely optimistic.",
]
PROMPT_POOLS = [
    [{"question":"The way to win me over is…","answer":"Be consistent and kind. That's it."},
     {"question":"My ideal Sunday looks like…","answer":"Filter coffee, a good book, walk by the river."}],
    [{"question":"A non-negotiable for me is…","answer":"Family values. Everything else we work through."},
     {"question":"I'm looking for someone who…","answer":"Knows what he wants and isn't afraid to say it."}],
    [{"question":"My friends describe me as…","answer":"Reliable, honest, and great with food."},
     {"question":"On weeknights you'll find me…","answer":"Winding down with music or helping at home."}],
    [{"question":"Something few people know about me…","answer":"I can eat gongura pachadi every single day."},
     {"question":"What I'm actually looking for…","answer":"A real partner. Someone to build a life with."}],
]

def rcoords(lat, lng, km):
    r = random.uniform(2, km)
    b = random.uniform(0, 2 * math.pi)
    return (round(lat + math.degrees(r / 6371) * math.sin(b), 6),
            round(lng + math.degrees(r / (6371 * math.cos(math.radians(lat)))) * math.cos(b), 6))

def rdob(mn=21, mx=33):
    return date.today() - timedelta(days=random.randint(mn * 365, mx * 365))

def make_profile():
    lat, lng = rcoords(GNT_BASE_LAT, GNT_BASE_LNG, MAX_RADIUS)
    city, country = random.choice(CITIES)
    photos = random.sample(PHOTOS, random.randint(3, 5))
    return {
        "id": str(uuid.uuid4()),
        "full_name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
        "bio": random.choice(BIOS), "is_active": True,
        "is_verified": random.random() < 0.7, "is_onboarded": True,
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 300)),
        "updated_at": datetime.now(timezone.utc), "date_of_birth": rdob(21, 33),
        "lat": lat, "lng": lng, "city": city, "country": country,
        "address": f"{city}, {country}", "height_cm": random.randint(152, 170),
        "gender_id": 224,
        "edu": random.choice([5,6,7,8,9,10,11]),
        "looking_for": random.choice([19,20,21,22,23]),
        "family_plans": random.choice([24,25,26,27]), "have_kids": random.choice([28,29,30]),
        "star_sign": random.choice(list(range(31,43))), "religion": random.choice(list(range(43,52))),
        "tier": random.choices(["free","pro"], weights=[60,40])[0],
        "vstatus": random.choices(["unverified","verified"], weights=[30,70])[0],
        "photos": json.dumps(photos),
        "interests": json.dumps([{"id": i} for i in random.sample(list(range(227,250)), random.randint(3,6))]),
        "languages": json.dumps([{"id": i} for i in random.sample(list(range(52,68)), random.randint(1,3))]),
        "lifestyle": json.dumps({"exercise": random.choice([1,2,3,4]), "drinking": random.choice([12,13,14]),
                                  "smoking": random.choice([15,16,17,18]), "diet": random.choice([286,287,288,289,290,291,292])}),
        "prompts": json.dumps(random.choice(PROMPT_POOLS)),
        "purpose": json.dumps([{"id": random.choice([19,20,21,22,23])}]),
    }

INSERT_SQL = """
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
    work_mode_enabled, filter_max_distance_km, filter_age_min,
    filter_age_max, filter_verified_only
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
    $17,$18,$19,$20,$21,$22,$23,$24,
    $25::jsonb,$26::jsonb,$27::jsonb,$28::jsonb,$29::jsonb,$30::jsonb,
    NULL,NULL,NULL,NULL,NULL,NULL,NULL,false,NULL,false,
    NULL,NULL,NULL,false
) ON CONFLICT DO NOTHING
"""

async def send_push(token, name, city, uid):
    import urllib.request
    payload = json.dumps({
        "to": token, "title": "Someone liked you 💚",
        "body": f"{name} from {city} liked your profile!",
        "sound": "default", "priority": "high", "channelId": "activity",
        "data": {"type": "liked_you", "liker_id": uid},
    }).encode()
    req = urllib.request.Request(
        "https://exp.host/--/api/v2/push/send", data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
        print(f"   push {name}: {res.get('data', {}).get('status', '?')}")
    except Exception as e:
        print(f"   push error ({name}): {e}")

async def seed():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASS, ssl=ssl_ctx,
    )
    random.seed()
    profiles = [make_profile() for _ in range(12)]

    ids = []
    print(f"\nInserting 12 female profiles near Guntur (≤{MAX_RADIUS}km)…")
    for p in profiles:
        try:
            await conn.execute(
                INSERT_SQL,
                p["id"], p["full_name"], p["bio"], p["is_active"], p["is_verified"],
                p["is_onboarded"], p["created_at"], p["updated_at"], p["date_of_birth"],
                p["lat"], p["lng"], p["city"], p["country"], p["address"],
                p["height_cm"], p["gender_id"], p["edu"], p["looking_for"],
                p["family_plans"], p["have_kids"], p["star_sign"], p["religion"],
                p["tier"], p["vstatus"],
                p["photos"], p["interests"], p["languages"], p["lifestyle"],
                p["prompts"], p["purpose"],
            )
            ids.append(p["id"])
            print(f"  ✅ {p['full_name']} ({p['city']})")
        except Exception as e:
            print(f"  ⚠  Failed {p['full_name']}: {e}")

    print(f"\nSeeding swipes → target…")
    for lid in ids:
        ts = datetime.now(timezone.utc)
        await conn.execute(
            "INSERT INTO swipes(swiper_id,swiped_id,direction,mode,created_at) "
            "VALUES($1::uuid,$2::uuid,'right','date',$3) "
            "ON CONFLICT(swiper_id,swiped_id,mode) DO NOTHING",
            lid, TARGET_USER_ID, ts,
        )
        await conn.execute(
            "INSERT INTO likes(liker_id,liked_id,created_at) "
            "VALUES($1::uuid,$2::uuid,$3) ON CONFLICT DO NOTHING",
            lid, TARGET_USER_ID, ts,
        )
    print(f"  {len(ids)} profiles swiped right on you")

    print(f"\nSending push notifications…")
    for lid in ids:
        p = next(x for x in profiles if x["id"] == lid)
        await send_push(TARGET_PUSH_TOKEN, p["full_name"].split()[0], p["city"], lid)
        await asyncio.sleep(0.25)

    await conn.close()
    print(f"\n🎉 Done — {len(ids)} new profiles added and liked you")

if __name__ == "__main__":
    asyncio.run(seed())
