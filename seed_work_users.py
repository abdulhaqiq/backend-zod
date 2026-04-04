"""
Seed work-mode users into the database.

Inserts:
  • 5 specific named profiles (the showcase users)
  • 20 randomised work profiles near London

ALL profiles have work_mode_enabled = True, a work headline, persona,
work experience, education, and work prompts.  Nothing is mocked in the
frontend — this is the single source of truth.

Run:
    cd backend
    python3 seed_work_users.py
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
DB = {
    "host":     settings.DB_HOST,
    "port":     settings.DB_PORT,
    "database": settings.DB_NAME,
    "user":     settings.DB_USERNAME,
    "password": settings.DB_PASSWORD,
    "ssl":      settings.DB_SSLMODE,
}

# ── London base coords ─────────────────────────────────────────────────────────
BASE_LAT      = 51.5074
BASE_LNG      = -0.1278
MAX_RADIUS_KM = 35

# ── Lookup IDs (from live DB) ──────────────────────────────────────────────────
GENDER_IDS             = [223, 224]
EDUCATION_IDS          = [5, 6, 7, 8, 9, 10, 11]
LOOKING_FOR_IDS        = [19, 20, 21, 22, 23]
FAMILY_PLANS_IDS       = [24, 25, 26, 27]
HAVE_KIDS_IDS          = [28, 29, 30]
STAR_SIGN_IDS          = list(range(31, 43))
RELIGION_IDS           = list(range(43, 52))
EXERCISE_IDS           = [1, 2, 3, 4]
DRINKING_IDS           = [12, 13, 14]
SMOKING_IDS            = [15, 16, 17, 18]
DIET_IDS               = [286, 287, 288, 289, 290, 291, 292]
INTEREST_IDS           = list(range(227, 250))
LANGUAGE_IDS           = list(range(52, 68))
WORK_COMMITMENT_IDS    = [72, 73, 74, 75]
WORK_EQUITY_IDS        = [76, 77, 78, 79, 80]
WORK_INDUSTRY_IDS      = list(range(81, 109))
WORK_SKILL_IDS         = list(range(109, 129))
WORK_MATCHING_GOAL_IDS = [68, 69, 70, 71]

MALE_PHOTOS   = [f"https://randomuser.me/api/portraits/men/{i}.jpg"   for i in range(1, 80)]
FEMALE_PHOTOS = [f"https://randomuser.me/api/portraits/women/{i}.jpg" for i in range(1, 80)]

LONDON_CITIES = [
    ("London", "UK"), ("London, Shoreditch", "UK"), ("London, Canary Wharf", "UK"),
    ("London, Islington", "UK"), ("London, Hackney", "UK"), ("London, Brixton", "UK"),
    ("London, Notting Hill", "UK"), ("London, Camden", "UK"), ("Greenwich", "UK"),
    ("Richmond", "UK"), ("Wimbledon", "UK"), ("Oxford", "UK"), ("Cambridge", "UK"),
]

# ── Random helpers ─────────────────────────────────────────────────────────────

def _coords(base_lat, base_lng, max_km):
    r       = random.uniform(0.5, max_km)
    bearing = random.uniform(0, 2 * math.pi)
    dlat    = math.degrees(r / 6371)
    dlng    = math.degrees(r / (6371 * math.cos(math.radians(base_lat))))
    return round(base_lat + dlat * math.sin(bearing), 6), round(base_lng + dlng * math.cos(bearing), 6)

def _dob(min_age=24, max_age=42):
    today = date.today()
    return today - timedelta(days=random.randint(min_age * 365, max_age * 365))

def _pick(lst, k):
    return random.sample(lst, min(k, len(lst)))

# ── Work prompt pools ─────────────────────────────────────────────────────────

WORK_PROMPT_POOLS = [
    [
        {"question": "What I'm building",
         "answer": "Compliance tooling for fintech companies across multiple jurisdictions. The problem is real, the market is underserved, and I've lived it from both sides."},
        {"question": "The co-founder I'm looking for",
         "answer": "Technical depth, customer empathy, and someone who'll tell me when I'm wrong. That last one is rare."},
    ],
    [
        {"question": "My startup in one line",
         "answer": "Real-time MRV platform for corporate carbon markets — Salesforce-style CRM for climate compliance."},
        {"question": "Why I'm doing this now",
         "answer": "I spent three years watching decisions get made slowly at a large company. I want to see what I can do without that."},
    ],
    [
        {"question": "The insight behind the idea",
         "answer": "Every logistics company I've talked to has the same data problem and they're all solving it in-house, badly."},
        {"question": "What I'm not good at",
         "answer": "Patience with processes that exist because they always have. I'm working on it."},
    ],
    [
        {"question": "What I bring to a founding team",
         "answer": "Enterprise sales experience, a network built over eight years, and a realistic view of how long things take."},
        {"question": "My biggest lesson from previous companies",
         "answer": "The first version of the product matters less than understanding why people would actually pay for it."},
    ],
    [
        {"question": "What success looks like to me",
         "answer": "Building something that people would be upset to lose. Revenue matters, but that's the test I use."},
        {"question": "I'm looking for someone who",
         "answer": "Has shipped something real, understands the difference between building and scaling, and doesn't need convincing that doing a startup is worth it."},
    ],
    [
        {"question": "My background in one line",
         "answer": "Built and led engineering teams at three companies, pre-seed to Series C. I know what breaks and when."},
        {"question": "The problem I'm obsessed with",
         "answer": "Healthcare providers spend 30% of their time on documentation. AI can fix that — I know the workflow well enough to build something that actually fits."},
    ],
    [
        {"question": "Why this, why me",
         "answer": "I've seen the inside of this industry for 6 years. The gap between what exists and what's possible is obvious to anyone who's done the job."},
        {"question": "What I'm looking for in a co-founder",
         "answer": "Someone I can disagree with productively. Complementary skills matter, but shared judgment matters more."},
    ],
    [
        {"question": "I've already done",
         "answer": "Customer discovery with 40+ potential users, an LOI from our first enterprise prospect, and a working prototype. I need someone to build the real thing."},
        {"question": "The unfair advantage I have",
         "answer": "I know the buyers personally. I've sold into this space for 5 years and I know exactly who picks up the phone and why."},
    ],
]

WORK_HEADLINES = [
    "Ex-Stripe engineer, building GPT-native ERP for SMBs. Raised $300k. Looking for GTM co-founder.",
    "Revolut product lead building a Gen Z wealth app. Need a technical co-founder who's shipped consumer apps.",
    "Serial founder, one Salesforce exit. Going after climate tech. Need a CTO who can own the stack.",
    "Mistral AI ML engineer. 3 papers on LLM inference. Building AI infra — need enterprise sales co-founder.",
    "Ex-McKinsey operator turned startup founder. Looking for a technical co-founder in the logistics space.",
    "Head of Product at a fintech scale-up. Curious whether I can build something better from zero.",
    "CTO with two failed startups and one acquisition. Now going again in health-tech. Smarter this time.",
    "VP Sales, 8 years in enterprise SaaS. Have a killer idea and a network. Need someone who can build.",
    "ML researcher turned product engineer. Obsessed with developer tooling. Looking for a GTM co-founder.",
    "Angel investor who wants to go back to operating. Have capital + network. Need a technical partner.",
    "Former startup lawyer turned operator. Building legal-tech that actually works for founders.",
    "Growth operator — 2 companies to 1M+ users. Looking for a technical co-founder to go again.",
    "Data scientist / PM hybrid. Closing the gap between what data says and what should be built.",
    "Built a community to 80k members organically. Now building the product layer on top of it.",
    "DevOps engineer with 10 years building infra. Targeting the platform engineering market.",
    "Head of Design at Figma. Looking for a seed-stage startup that takes design seriously.",
    "Former VC-backed founder (Series A). Going again in climate data — this time leaner.",
    "Ex-Salesforce AE turned founder. Know the enterprise buyer inside out. Need a technical co-founder.",
    "NLP researcher at a top AI lab. Productising inference optimisation. Need a GTM partner.",
    "COO at two early-stage companies. Expert at moving fast with incomplete information.",
]

COMPANIES = [
    "Google", "Stripe", "Revolut", "Meta", "Mistral AI", "OpenAI", "Monzo", "Airbnb",
    "Uber", "Salesforce", "McKinsey", "Goldman Sachs", "DeepMind", "Figma", "Notion",
    "Intercom", "Hugging Face", "Y Combinator", "Accenture", "Amazon", "Apple",
    "LinkedIn", "Spotify", "Shopify", "Plaid", "Sequoia", "a16z", "Palantir",
]
JOB_TITLES = [
    "Staff Engineer", "Senior Engineer", "ML Engineer", "CTO", "VP of Product",
    "Head of Product", "Product Lead", "Senior PM", "Founder & CEO", "Co-Founder",
    "VP Sales", "Head of Design", "Principal Engineer", "Research Engineer",
    "Data Scientist", "Growth Lead", "COO", "Operations Lead", "Engineering Manager",
]
UNIVERSITIES = [
    ("MIT", "Computer Science"),
    ("Stanford University", "Computer Science / AI"),
    ("University of Cambridge", "Computer Science"),
    ("University of Oxford", "Computer Science"),
    ("Imperial College London", "EEE / AI"),
    ("UCL", "Computer Science"),
    ("Harvard Business School", "Business Administration"),
    ("London Business School", "MBA"),
    ("University of Edinburgh", "Informatics"),
    ("ETH Zurich", "Computer Science"),
    ("Carnegie Mellon University", "Machine Learning"),
    ("UC Berkeley", "EECS"),
    ("Yale University", "Economics"),
    ("LSE", "Economics"),
    ("King's College London", "Mathematics"),
    ("University of Warwick", "Mathematics & Computer Science"),
    ("University of Bristol", "Computer Science"),
    ("University of Manchester", "Software Engineering"),
    ("Royal College of Art", "Interaction Design"),
    ("Goldsmiths", "Creative Computing"),
]
DEGREES = ["Bachelor's", "Master's", "PhD", "MBA", "MEng", "MPhil", "MSc"]

PERSONAS  = ["founder", "job_seeker", "both"]
FIRST_NAMES_M = [
    "Alex", "Jordan", "Morgan", "Riley", "Taylor", "Blake", "Quinn", "Sage",
    "Elliot", "Kai", "River", "Phoenix", "Parker", "Logan", "Jamie",
    "Marcus", "Nathan", "Felix", "Leon", "Tobias",
]
FIRST_NAMES_F = [
    "Priya", "Sarah", "Emma", "Olivia", "Zara", "Nadia", "Yuna", "Sofia",
    "Maya", "Jade", "Aria", "Leila", "Freya", "Isla", "Mia",
    "Valentina", "Rosa", "Chloe", "Avery", "Skylar",
]
LAST_NAMES = [
    "Chen", "Sharma", "Kim", "Liu", "Osei", "Ahmed", "Patel", "Singh",
    "Williams", "Taylor", "Johnson", "Brown", "Davis", "Wilson", "Moore",
    "Thompson", "Young", "Lewis", "Walker", "Hall",
]

# ── 5 specific showcase profiles ───────────────────────────────────────────────

SPECIFIC_PROFILES = [
    {
        "full_name":    "Alex Chen",
        "linkedin_url": "https://www.linkedin.com/in/alex-chen-stripe",
        "gender_id":    223,
        "photos":       ["https://randomuser.me/api/portraits/men/32.jpg"],
        "work_photos":  ["https://randomuser.me/api/portraits/men/32.jpg"],
        "work_headline": "Ex-Stripe Staff Engineer building the GPT-native ERP. $40k ARR, 3 paying customers. Looking for a GTM co-founder.",
        "work_persona": "founder",
        "work_prompts": [
            {"question": "My startup in one line",
             "answer": "Replace 5 SaaS tools with one GPT-native ERP — already at $40k ARR with 3 paying SMBs."},
            {"question": "The co-founder I'm looking for",
             "answer": "A sales-obsessed operator who's closed enterprise deals and can build a GTM engine from zero."},
        ],
        "work_experience": [
            {"job_title": "Staff Engineer", "company": "Stripe",  "start_year": "2019", "end_year": "",     "current": True},
            {"job_title": "Senior Engineer","company": "Plaid",   "start_year": "2017", "end_year": "2019", "current": False},
            {"job_title": "Software Engineer","company":"Google", "start_year": "2015", "end_year": "2017", "current": False},
        ],
        "education": [
            {"institution": "MIT",      "degree": "Bachelor's", "course": "Computer Science", "grad_year": "2015"},
            {"institution": "Stanford", "degree": "Master's",   "course": "CS / AI",          "grad_year": "2016"},
        ],
    },
    {
        "full_name":    "Priya Sharma",
        "linkedin_url": "https://www.linkedin.com/in/priya-sharma-fintech",
        "gender_id":    224,
        "photos":       ["https://randomuser.me/api/portraits/women/44.jpg"],
        "work_photos":  ["https://randomuser.me/api/portraits/women/44.jpg"],
        "work_headline": "5 yrs fintech product at Revolut & Monzo. Building a B2C wealth app for Gen Z — open to a technical co-founder who's shipped consumer apps.",
        "work_persona": "founder",
        "work_prompts": [
            {"question": "What I bring to the founding team",
             "answer": "Deep fintech domain, a network of 200+ London angels, and product instincts sharpened by shipping to 10M+ users."},
            {"question": "The problem I'm obsessed with",
             "answer": "Gen Z earns more than any generation but saves less. I want to fix that with behavioural-finance-backed tooling."},
        ],
        "work_experience": [
            {"job_title": "Product Lead — Growth", "company": "Revolut", "start_year": "2021", "end_year": "",     "current": True},
            {"job_title": "Senior PM",             "company": "Monzo",   "start_year": "2019", "end_year": "2021", "current": False},
        ],
        "education": [
            {"institution": "University College London", "degree": "Bachelor's", "course": "Economics", "grad_year": "2018"},
        ],
    },
    {
        "full_name":    "Jordan Kim",
        "linkedin_url": "https://www.linkedin.com/in/jordan-kim-founder",
        "gender_id":    223,
        "photos":       ["https://randomuser.me/api/portraits/men/55.jpg"],
        "work_photos":  ["https://randomuser.me/api/portraits/men/55.jpg"],
        "work_headline": "Serial founder, one exit (acquired by Salesforce '22). Now going after climate tech. Need a CTO who can own the full stack.",
        "work_persona": "founder",
        "work_prompts": [
            {"question": "Why climate, why now",
             "answer": "Carbon markets are broken. Corporates need a real-time MRV platform — I know the buyers, I just need someone who can build it."},
            {"question": "My co-founder must be",
             "answer": "Full-stack engineer who's built data-heavy B2B SaaS. Startup experience required — you shouldn't need to be sold on the lifestyle."},
        ],
        "work_experience": [
            {"job_title": "Co-Founder & CEO",  "company": "GreenOps (acq. Salesforce)", "start_year": "2018", "end_year": "2022", "current": False},
            {"job_title": "Account Executive", "company": "Salesforce",                 "start_year": "2016", "end_year": "2018", "current": False},
            {"job_title": "Analyst",           "company": "McKinsey & Company",         "start_year": "2014", "end_year": "2016", "current": False},
        ],
        "education": [
            {"institution": "Harvard Business School", "degree": "MBA",         "course": "Business Administration", "grad_year": "2014"},
            {"institution": "Yale University",         "degree": "Bachelor's",  "course": "Economics",               "grad_year": "2012"},
        ],
    },
    {
        "full_name":    "Sarah Liu",
        "linkedin_url": "https://www.linkedin.com/in/sarah-liu-ml",
        "gender_id":    224,
        "photos":       ["https://randomuser.me/api/portraits/women/68.jpg"],
        "work_photos":  ["https://randomuser.me/api/portraits/women/68.jpg"],
        "work_headline": "ML Research Engineer @ Mistral AI. 3 published papers on LLM inference. Building AI infra — looking for an enterprise sales co-founder.",
        "work_persona": "founder",
        "work_prompts": [
            {"question": "My startup thesis",
             "answer": "Inference cost is the new cloud bill. I've cut it 60% with speculative decoding + quantisation — now I want to productise that."},
            {"question": "The co-founder I need",
             "answer": "Someone who's sold to CTOs at mid-market tech companies and understands the enterprise AI procurement cycle."},
        ],
        "work_experience": [
            {"job_title": "ML Research Engineer","company": "Mistral AI",    "start_year": "2023", "end_year": "",     "current": True},
            {"job_title": "Research Intern",     "company": "DeepMind",      "start_year": "2022", "end_year": "2022", "current": False},
            {"job_title": "ML Engineer",         "company": "Hugging Face",  "start_year": "2021", "end_year": "2022", "current": False},
        ],
        "education": [
            {"institution": "University of Cambridge",  "degree": "Master's (MPhil)", "course": "Machine Learning", "grad_year": "2021"},
            {"institution": "Imperial College London",  "degree": "MEng",             "course": "EEE / AI",         "grad_year": "2020"},
        ],
    },
    {
        "full_name":    "Marcus Osei",
        "linkedin_url": "https://www.linkedin.com/in/marcus-osei-design",
        "gender_id":    223,
        "photos":       ["https://randomuser.me/api/portraits/men/78.jpg"],
        "work_photos":  ["https://randomuser.me/api/portraits/men/78.jpg"],
        "work_headline": "Head of Design at Figma. Looking to join a seed-stage startup as founding designer or early hire.",
        "work_persona": "job_seeker",
        "work_prompts": [
            {"question": "What I'm looking for",
             "answer": "A seed or Series A startup where design is a first-class citizen. Fintech, AI tools, or developer infrastructure."},
            {"question": "My design superpower",
             "answer": "Turning complex technical workflows into things people enjoy using. Ask me about the Figma plugins redesign that drove 40% activation uplift."},
        ],
        "work_experience": [
            {"job_title": "Head of Design — Plugins","company": "Figma",     "start_year": "2021", "end_year": "",     "current": True},
            {"job_title": "Senior Product Designer", "company": "Notion",    "start_year": "2019", "end_year": "2021", "current": False},
            {"job_title": "Product Designer",        "company": "Intercom",  "start_year": "2016", "end_year": "2019", "current": False},
        ],
        "education": [
            {"institution": "Royal College of Art",         "degree": "Master's",  "course": "Interaction Design", "grad_year": "2016"},
            {"institution": "University of the Arts London","degree": "Bachelor's","course": "Graphic Design",     "grad_year": "2014"},
        ],
    },
]


# ── Profile builders ───────────────────────────────────────────────────────────

def _lifestyle():
    return {
        "exercise": random.choice(EXERCISE_IDS),
        "drinking": random.choice(DRINKING_IDS),
        "smoking":  random.choice(SMOKING_IDS),
        "diet":     random.choice(DIET_IDS),
    }


def _random_experience():
    exp = []
    num = random.randint(2, 3)
    year = random.randint(2008, 2014)
    for _ in range(num):
        duration  = random.randint(1, 4)
        end_year  = year + duration
        is_current = (_ == 0) and (random.random() < 0.5)
        exp.append({
            "job_title":  random.choice(JOB_TITLES),
            "company":    random.choice(COMPANIES),
            "start_year": str(end_year),
            "end_year":   "" if is_current else str(end_year + random.randint(1, 3)),
            "current":    is_current,
        })
        year = end_year + random.randint(0, 1)
    return exp


def _random_education():
    edu = []
    for _ in range(random.randint(1, 2)):
        uni, field = random.choice(UNIVERSITIES)
        edu.append({
            "institution": uni,
            "degree":      random.choice(DEGREES),
            "course":      field,
            "grad_year":   str(random.randint(2009, 2022)),
        })
    return edu


def make_specific_profile(spec: dict) -> dict:
    lat, lng = _coords(BASE_LAT, BASE_LNG, 20)
    city, country = random.choice(LONDON_CITIES)
    is_male = spec["gender_id"] == 223
    industries      = _pick(WORK_INDUSTRY_IDS, random.randint(2, 4))
    skills          = _pick(WORK_SKILL_IDS,    random.randint(2, 5))
    matching_goals  = _pick(WORK_MATCHING_GOAL_IDS, random.randint(1, 2))

    return {
        "id":                       str(uuid.uuid4()),
        "full_name":                spec["full_name"],
        "bio":                      "",
        "is_active":                True,
        "is_verified":              True,
        "is_onboarded":             True,
        "created_at":               datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30)),
        "updated_at":               datetime.now(timezone.utc),
        "date_of_birth":            _dob(26, 40),
        "latitude":                 lat,
        "longitude":                lng,
        "city":                     city,
        "country":                  country,
        "address":                  f"{city}, {country}",
        "height_cm":                random.randint(162, 190),
        "gender_id":                spec["gender_id"],
        "education_level_id":       random.choice(EDUCATION_IDS),
        "looking_for_id":           random.choice(LOOKING_FOR_IDS),
        "family_plans_id":          random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":             random.choice(HAVE_KIDS_IDS),
        "star_sign_id":             random.choice(STAR_SIGN_IDS),
        "religion_id":              random.choice(RELIGION_IDS),
        "subscription_tier":        "pro",
        "verification_status":      "verified",
        "photos":                   json.dumps(spec["photos"]),
        "work_photos":              json.dumps(spec.get("work_photos", spec["photos"])),
        "interests":                json.dumps([{"id": i} for i in _pick(INTEREST_IDS, 5)]),
        "languages":                json.dumps([{"id": i} for i in _pick(LANGUAGE_IDS, 2)]),
        "lifestyle":                json.dumps(_lifestyle()),
        "prompts":                  json.dumps([]),
        "purpose":                  json.dumps([{"id": random.choice(LOOKING_FOR_IDS)}]),
        "work_prompts":             json.dumps(spec["work_prompts"]),
        "work_headline":            spec["work_headline"],
        "work_persona":             spec["work_persona"],
        "work_mode_enabled":        True,
        "work_matching_goals":      json.dumps([{"id": i} for i in matching_goals]),
        "work_commitment_level_id": random.choice(WORK_COMMITMENT_IDS),
        "work_equity_split_id":     random.choice(WORK_EQUITY_IDS),
        "work_industries":          json.dumps([{"id": i} for i in industries]),
        "work_skills":              json.dumps([{"id": i} for i in skills]),
        "work_are_you_hiring":      False,
        "work_experience":          json.dumps(spec["work_experience"]),
        "education":                json.dumps(spec["education"]),
        "linkedin_url":             spec.get("linkedin_url"),
        "linkedin_verified":        spec.get("linkedin_url") is not None,
        "filter_max_distance_km":   None,
        "filter_age_min":           None,
        "filter_age_max":           None,
        "filter_verified_only":     False,
    }


def make_random_work_profile(idx: int) -> dict:
    is_male    = random.random() < 0.5
    first_name = random.choice(FIRST_NAMES_M if is_male else FIRST_NAMES_F)
    last_name  = random.choice(LAST_NAMES)
    gender_id  = 223 if is_male else 224
    photos     = [random.choice(MALE_PHOTOS if is_male else FEMALE_PHOTOS)]

    lat, lng       = _coords(BASE_LAT, BASE_LNG, MAX_RADIUS_KM)
    city, country  = random.choice(LONDON_CITIES)
    industries     = _pick(WORK_INDUSTRY_IDS, random.randint(2, 4))
    skills         = _pick(WORK_SKILL_IDS,    random.randint(2, 5))
    matching_goals = _pick(WORK_MATCHING_GOAL_IDS, random.randint(1, 2))
    persona        = random.choice(PERSONAS)

    return {
        "id":                       str(uuid.uuid4()),
        "full_name":                f"{first_name} {last_name}",
        "bio":                      "",
        "is_active":                True,
        "is_verified":              random.random() < 0.70,
        "is_onboarded":             True,
        "created_at":               datetime.now(timezone.utc) - timedelta(days=random.randint(1, 120)),
        "updated_at":               datetime.now(timezone.utc),
        "date_of_birth":            _dob(24, 44),
        "latitude":                 lat,
        "longitude":                lng,
        "city":                     city,
        "country":                  country,
        "address":                  f"{city}, {country}",
        "height_cm":                random.randint(158, 195),
        "gender_id":                gender_id,
        "education_level_id":       random.choice(EDUCATION_IDS),
        "looking_for_id":           random.choice(LOOKING_FOR_IDS),
        "family_plans_id":          random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":             random.choice(HAVE_KIDS_IDS),
        "star_sign_id":             random.choice(STAR_SIGN_IDS),
        "religion_id":              random.choice(RELIGION_IDS),
        "subscription_tier":        random.choices(["free", "pro"], weights=[50, 50])[0],
        "verification_status":      random.choices(["unverified", "verified"], weights=[30, 70])[0],
        "photos":                   json.dumps(photos),
        "work_photos":              json.dumps(photos),
        "interests":                json.dumps([{"id": i} for i in _pick(INTEREST_IDS, 4)]),
        "languages":                json.dumps([{"id": i} for i in _pick(LANGUAGE_IDS, 2)]),
        "lifestyle":                json.dumps(_lifestyle()),
        "prompts":                  json.dumps([]),
        "purpose":                  json.dumps([{"id": random.choice(LOOKING_FOR_IDS)}]),
        "work_prompts":             json.dumps(random.choice(WORK_PROMPT_POOLS)),
        "work_headline":            random.choice(WORK_HEADLINES),
        "work_persona":             persona,
        "work_mode_enabled":        True,
        "work_matching_goals":      json.dumps([{"id": i} for i in matching_goals]),
        "work_commitment_level_id": random.choice(WORK_COMMITMENT_IDS),
        "work_equity_split_id":     random.choice(WORK_EQUITY_IDS),
        "work_industries":          json.dumps([{"id": i} for i in industries]),
        "work_skills":              json.dumps([{"id": i} for i in skills]),
        "work_are_you_hiring":      random.random() < 0.30,
        "work_experience":          json.dumps(_random_experience()),
        "education":                json.dumps(_random_education()),
        "linkedin_url":             f"https://www.linkedin.com/in/{first_name.lower()}-{last_name.lower()}-{idx:04d}" if random.random() < 0.75 else None,
        "linkedin_verified":        False,
        "filter_max_distance_km":   None,
        "filter_age_min":           None,
        "filter_age_max":           None,
        "filter_verified_only":     False,
    }


# ── Insert SQL ─────────────────────────────────────────────────────────────────

INSERT_SQL = """
INSERT INTO users (
    id, full_name, bio, is_active, is_verified, is_onboarded,
    created_at, updated_at, date_of_birth,
    latitude, longitude, city, country, address,
    height_cm, gender_id, education_level_id, looking_for_id,
    family_plans_id, have_kids_id, star_sign_id, religion_id,
    subscription_tier, verification_status,
    photos, work_photos, interests, languages, lifestyle, prompts, purpose,
    work_prompts, work_headline, work_persona, work_mode_enabled,
    work_matching_goals, work_commitment_level_id, work_equity_split_id,
    work_industries, work_skills, work_are_you_hiring,
    work_experience, education,
    linkedin_url, linkedin_verified,
    filter_max_distance_km, filter_age_min, filter_age_max, filter_verified_only
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,
    $10,$11,$12,$13,$14,$15,$16,$17,$18,
    $19,$20,$21,$22,$23,$24,
    $25::jsonb,$26::jsonb,$27::jsonb,$28::jsonb,$29::jsonb,$30::jsonb,$31::jsonb,
    $32::jsonb,$33,$34,$35,
    $36::jsonb,$37,$38,
    $39::jsonb,$40::jsonb,$41,
    $42::jsonb,$43::jsonb,
    $44,$45,
    $46,$47,$48,$49
)
ON CONFLICT DO NOTHING
"""


async def seed():
    print("Connecting to database…")
    conn = await asyncpg.connect(**DB)

    random.seed(None)
    profiles = (
        [make_specific_profile(s) for s in SPECIFIC_PROFILES] +
        [make_random_work_profile(i) for i in range(20)]
    )

    print(f"Inserting {len(profiles)} work profiles ({len(SPECIFIC_PROFILES)} specific + 20 random)…")

    inserted = 0
    for p in profiles:
        try:
            await conn.execute(
                INSERT_SQL,
                p["id"], p["full_name"], p["bio"], p["is_active"], p["is_verified"],
                p["is_onboarded"], p["created_at"], p["updated_at"], p["date_of_birth"],
                p["latitude"], p["longitude"], p["city"], p["country"], p["address"],
                p["height_cm"], p["gender_id"], p["education_level_id"], p["looking_for_id"],
                p["family_plans_id"], p["have_kids_id"], p["star_sign_id"], p["religion_id"],
                p["subscription_tier"], p["verification_status"],
                p["photos"], p["work_photos"], p["interests"], p["languages"],
                p["lifestyle"], p["prompts"], p["purpose"],
                p["work_prompts"], p["work_headline"], p["work_persona"], p["work_mode_enabled"],
                p["work_matching_goals"], p["work_commitment_level_id"], p["work_equity_split_id"],
                p["work_industries"], p["work_skills"], p["work_are_you_hiring"],
                p["work_experience"], p["education"],
                p["linkedin_url"], p["linkedin_verified"],
                p["filter_max_distance_km"], p["filter_age_min"], p["filter_age_max"],
                p["filter_verified_only"],
            )
            inserted += 1
            print(f"  ✓  {p['full_name']}")
        except Exception as e:
            print(f"  ✗  {p['full_name']}: {e}")

    # Patch any existing work-mode users that still have linkedin_url = NULL
    # by assigning a plausible URL derived from their name.
    print("\nPatching existing work-mode users with missing LinkedIn URLs…")
    rows = await conn.fetch(
        "SELECT id, full_name FROM users WHERE work_mode_enabled = TRUE AND linkedin_url IS NULL"
    )
    patched = 0
    for row in rows:
        name_slug = row["full_name"].lower().replace(" ", "-")
        url = f"https://www.linkedin.com/in/{name_slug}"
        await conn.execute(
            "UPDATE users SET linkedin_url = $1, linkedin_verified = TRUE WHERE id = $2",
            url, row["id"],
        )
        patched += 1
    print(f"  Patched {patched} existing user(s).")

    await conn.close()
    print(f"\n🎉 Done — {inserted}/{len(profiles)} work profiles inserted.")
    print("   All profiles have work_mode_enabled = True and will appear in the work feed.")


if __name__ == "__main__":
    asyncio.run(seed())
