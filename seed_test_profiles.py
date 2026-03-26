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
    "Architect by training, serial wanderer by choice. I sketch buildings I'll probably never build and eat at restaurants I can't afford. Looking for someone who's good at picking the wine.",
    "I read slower than I should, cook better than I admit, and have genuinely strong opinions about sourdough hydration. Also into long drives with no destination.",
    "Spent three years working in Tokyo, came back with better knife skills and zero idea what I want. Figured meeting interesting people was a good start.",
    "I run half-marathons badly but consistently. Software engineer who actually enjoys her job, which apparently makes me unusual. Pavlova enthusiast.",
    "My flat is half bookshelf, half plant situation. I work in documentary film and spend too much time thinking about stories that haven't been told yet.",
    "Former competitive swimmer turned product manager. I still wake up at 6am out of muscle memory and I've learned to make peace with that.",
    "I'll remember your coffee order after one conversation. Nurse, part-time ceramicist, full-time over-tipper at good restaurants.",
    "I'm funnier in person. Physics PhD, now in climate tech. My idea of a perfect evening is a long dinner with nowhere to be afterwards.",
    "I travelled solo for two years and it made me better at being with people, not worse. Currently back in London and slightly adjusting to the commute.",
    "I design things during the week and fall asleep at live jazz on weekends. Looking for someone who appreciates both and doesn't make me choose.",
    "Chef who learned to cook in a restaurant kitchen at 17 and never really left. I find it hard to eat bad food quietly. This is either a red flag or a green one.",
    "I take cold showers willingly, which I've been told is a personality. Marine conservation researcher who makes too much pasta on Sunday.",
    "Grew up between Lagos and London, which makes me very good at adapting and very particular about jollof rice. Finance by day, music production by night.",
    "I over-research restaurants, under-pack for trips, and always stay for one more drink. Physiotherapist. I will at some point ask about your posture.",
    "Writer trying to finish the same novel for three years. I know the problem — I'm just not sure I want to solve it yet. Also good at recommending books.",
    "Software engineer who takes long lunch breaks and long hikes. I like people who can disagree with me without it becoming a thing.",
    "Spent my twenties chasing ambition and my early thirties figuring out what I actually want. Somewhere in there I learned to sail, which helps.",
    "I make lists. I lose lists. I make new lists. Structural engineer with a weakness for old bookshops and new coffee places in the same city.",
    "Used to think I was an introvert until I started hosting dinner parties. Turns out I just needed a reason to talk to people. Graphic designer.",
    "Archaeologist who works on Roman sites in the summer and teaches at a university in the winter. I have niche references and I will use them.",
]

WORK_BIOS = [
    "Staff engineer at a Series B company. I've spent eight years in backend infrastructure and I'm starting to want a different kind of problem to solve.",
    "Head of Product at a payments company. I've shipped things that people use every day and I'm looking for someone who wants to build something from scratch.",
    "I sold a small SaaS company in 2021 and spent a year figuring out what I actually wanted to do next. Now working on something in climate data. Earlier stage than I've been before.",
    "Machine learning researcher who moved from academia to industry three years ago. I work on applied NLP and I'm interested in what gets built with it, not just the models.",
    "I've started two companies. One failed, one got acquired. I learned more from the first one. Now looking for a technical co-founder to go again.",
    "VP of Product at a fintech company. The job is good. I'm curious whether something I build from zero could be better.",
    "I've been an operator — COO, then CEO — at two early-stage companies. The thing I'm best at is making decisions with incomplete information quickly.",
    "I worked at McKinsey for four years and left to join a startup, which I should have done sooner. I understand enterprise sales and I know how to hire for it.",
    "I'm a developer tools engineer who has spent most of my career at companies building for other developers. I have opinions about what's missing.",
    "Growth operator. I've run acquisition at two companies that scaled to 1M+ users. I'm good at finding the channel that everyone else missed.",
    "I'm a CTO who has hired and led engineering teams between 5 and 80 people. I know what breaks and when, and I'm faster at fixing it than I used to be.",
    "Angel investor with a small portfolio. I want to go back to operating — I miss having skin in the game on a single thing.",
    "Former startup lawyer turned operator. I understand cap tables, compliance, and how to move fast without breaking things that matter.",
    "Data scientist who transitioned into product. I close the gap between what the data says and what should be built. That's a rare thing and I know it.",
    "I built a community platform to 80k members organically. Now I want to build the product layer on top of it with someone who knows what they're doing technically.",
]

PROMPT_POOLS = [
    [{"question": "The way to win me over is…", "answer": "Remember something I mentioned once. That's it. That's the whole thing."},
     {"question": "My ideal Sunday looks like…", "answer": "Market in the morning, no plans by noon, somewhere good for dinner that we just find by walking."}],
    [{"question": "Don't hate me if I…", "answer": "Reread the same three books every year. I know what I like."},
     {"question": "A non-negotiable for me is…", "answer": "Good coffee. I will go out of my way for it and I'm not embarrassed about that."}],
    [{"question": "I'm looking for someone who…", "answer": "Has a thing — a book, a sport, a city, a subject — that genuinely lights them up when they talk about it."},
     {"question": "My friends describe me as…", "answer": "The one who shows up, remembers the thing you said three months ago, and brings snacks."}],
    [{"question": "Two truths and a lie…", "answer": "I've worked in four countries. I don't own a TV. I've never been on a rollercoaster."},
     {"question": "I know the best spot for…", "answer": "Breakfast. Wherever I live, finding the best breakfast place is the first thing I do."}],
    [{"question": "Something most people don't know about me…", "answer": "I'm much quieter one-on-one than I seem in a group. I like that about myself."},
     {"question": "What I'm actually looking for…", "answer": "Someone I can be boring with. Not every night has to be an event."}],
    [{"question": "The last thing I changed my mind about…", "answer": "Whether cities or countryside are better. Still don't have an answer."},
     {"question": "I go too far when it comes to…", "answer": "Research before making a decision. I have compared hotel rooms for hours."}],
    [{"question": "A perfect first date…", "answer": "Long walk, somewhere interesting to eat, no agenda. Easy to extend if it's good, easy to end if it's not."},
     {"question": "What I bring to a relationship…", "answer": "Loyalty, honest conversation, and the ability to sit in comfortable silence."}],
    [{"question": "My most unpopular opinion…", "answer": "Most things are better without background music. Including dinner."},
     {"question": "I get irrationally annoyed by…", "answer": "Vague plans. 'Let's hang sometime' means nothing. Pick a day."}],
    [{"question": "On weeknights you'll find me…", "answer": "Cooking something that takes too long, listening to something I've heard before. It's a good system."},
     {"question": "The conversation topic I'll never get bored of…", "answer": "Why people are the way they are. I could talk about that forever."}],
    [{"question": "I'm happiest when…", "answer": "There's no rush. A long meal, nowhere to be, someone worth talking to."},
     {"question": "One thing I've learned the hard way…", "answer": "You can't reason people into caring about you. Either they do or they don't."}],
]

WORK_PROMPT_POOLS = [
    [{"question": "What I'm building", "answer": "Compliance tooling for fintech companies operating across multiple jurisdictions. The problem is real, the market is underserved, and I've lived it from both sides."},
     {"question": "What I'm looking for in a co-founder", "answer": "Technical depth, customer empathy, and someone who'll tell me when I'm wrong. That last one is rare."}],
    [{"question": "What I've learned the hard way", "answer": "The first version of the product matters less than understanding why people would actually pay for it."},
     {"question": "What I bring", "answer": "Enterprise sales experience, a network built over eight years in the industry, and a realistic view of how long things take."}],
    [{"question": "The problem I'm working on", "answer": "Healthcare providers spend 30% of their time on documentation. AI can fix that. I know the workflow well enough to build something that fits."},
     {"question": "Why I'm doing this now", "answer": "I spent three years at a large company watching decisions get made slowly. I want to see what I can do without that."}],
    [{"question": "My background in one sentence", "answer": "Built and ran engineering teams at three companies, from pre-seed to Series C. I know what breaks at each stage."},
     {"question": "What success looks like to me", "answer": "Building something that people would be upset to lose. Revenue matters, but that's the test I use."}],
    [{"question": "The insight behind the idea", "answer": "Every logistics company I've talked to has the same data problem and they're all solving it in-house, badly."},
     {"question": "What I'm not good at", "answer": "Patience with processes that exist because they've always existed. I'm working on it."}],
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
    "Civil engineer working on Vision 2030 projects. When I'm not on-site I'm in a coffee shop somewhere in Al Olaya with a book I'll probably not finish.",
    "I grew up in Riyadh and spent four years studying abroad, which gave me strong opinions about both places. Dentist. I notice your teeth but I won't say anything.",
    "Pilot based in Riyadh. I have strange hours and a deep appreciation for quiet mornings. Looking for someone who's easy to be around.",
    "Fashion designer who runs a small atelier in Hittin. I find most people more interesting than they think they are.",
    "Entrepreneur in the fintech space. I read more than I socialise, which people find surprising. I make very good kabsa.",
    "Software engineer who moved back to Riyadh after five years in London. The food is better here. The weather, debatable.",
    "I teach at a university and consult on the side. I have too many unread articles saved and a habit of asking follow-up questions.",
    "Doctor at King Faisal. My weekends look like: coffee, long drive, a good podcast, something grilled for dinner. That's it. That's the pitch.",
    "Marketing director at a media company. I'm more curious than I appear in writing. Also genuinely interested in people — not just their headlines.",
    "Architect who split her time between Riyadh and Dubai before landing here properly. I'm particular about spaces and less particular about everything else.",
    "Yoga instructor who started taking it seriously after an injury. I like people who have a thing they're serious about, whatever it is.",
    "I work in investment banking and balance it out by playing oud badly on weekends. Looking for someone who's interesting to talk to over a long dinner.",
    "Photographer who does it for the work, not the followers. I live in Al Nakheel and know every decent coffee spot within 5km.",
    "Startup founder in health tech. Three years in, learning something new every week. Better at listening than I used to be.",
    "Interior designer who genuinely cares about how spaces feel. I'm told I'm good at making people comfortable. I'm trying to apply that more broadly.",
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
    "Cody", "Tyler", "Chase", "Bryce", "Garrett",
    "Trevor", "Blake", "Colton", "Tanner", "Dakota",
    "Jared", "Kyle", "Dustin", "Austin", "Ryan",
]
US_LAST_NAMES = [
    "Mitchell", "Campbell", "Parker", "Evans", "Edwards",
    "Collins", "Stewart", "Morris", "Rogers", "Reed",
    "Cook", "Morgan", "Bell", "Murphy", "Bailey",
    "Rivera", "Cooper", "Richardson", "Cox", "Howard",
]
US_DATE_BIOS = [
    "UX designer at a startup in Silver Lake. I moved from New York three years ago and I'm still adjusting to the pace, which is slower and better. I hike before most people wake up.",
    "I'm a marine biologist at Scripps. I study cetacean communication, which is as strange and time-consuming as it sounds. Outside of work I'm fairly low-maintenance.",
    "Software engineer. I work remotely, which means I spend a lot of time thinking about where I am. Currently Venice Beach. Previously Austin, Amsterdam, and a van for six months.",
    "Film director who pays the bills doing commercial work and makes the stuff I actually care about in between. I'm in the middle of a short that keeps getting longer.",
    "I run a small pottery studio in Echo Park. I also teach twice a week. The work is physical and quiet and I find it necessary.",
    "Nurse practitioner in San Diego. Twelve-hour shifts make you very clear about what you want from the hours that aren't work. I want them to be real.",
    "Structural engineer. I like the part where something that didn't exist becomes something that does. I also like the beach, which is why I'm in LA and not Chicago.",
    "I left investment banking to go to culinary school at 28, which my parents still think was a mistake. I run the kitchen at a small restaurant in Pasadena. I was right.",
    "Environmental lawyer. I spend my days on cases that move slowly and matter a lot. I cope with long hikes and cooking things from scratch.",
    "Sports journalist. I cover the Clippers, which is either a blessing or a curse depending on the season. I'm good at sitting with uncertainty.",
    "I work in urban planning and spend my days arguing about zoning, which is more interesting than it sounds. I grew up in Santa Barbara and came back after ten years away.",
    "Photographer who shoots documentary work and corporate stuff to fund it. I'm working on a long-form project about Salton Sea communities that I've been on for two years.",
    "Physical therapist in Irvine. I like helping people move better. Outside of work I run trails in Laguna and cook elaborate dinners for small groups of people.",
    "Product manager at a climate tech company. I care about the mission and try not to be insufferable about it. I make good tacos and I'll prove it.",
    "I teach high school history in Pasadena. It's the right job for me. I'm serious about it, and about the long weekend hikes that keep me sane.",
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

# ── Dubai / UAE seed data ─────────────────────────────────────────────────────
DXB_BASE_LAT   = 25.2048
DXB_BASE_LNG   = 55.2708
DXB_MAX_RADIUS = 80  # km — covers Dubai, Sharjah, Abu Dhabi corridor

DUBAI_MALE_NAMES = [
    "Zayed", "Hamdan", "Rashid", "Khalid", "Mohammed",
    "Omar", "Saif", "Ahmad", "Tariq", "Yousef",
    "Bilal", "Hassan", "Kareem", "Nabil", "Faris",
    "Ryan", "Jake", "Marcus", "Daniel", "Luca",
]
DUBAI_FEMALE_NAMES = [
    "Fatima", "Mariam", "Latifa", "Hind", "Shamma",
    "Aisha", "Noura", "Sara", "Dana", "Reem",
    "Jessica", "Sophie", "Emma", "Priya", "Ananya",
    "Nadia", "Layla", "Yasmin", "Zara", "Hana",
]
DUBAI_LAST_NAMES = [
    "Al Maktoum", "Al Nahyan", "Al Rashid", "Al Mansoori", "Al Falasi",
    "Al Mazrouei", "Al Kaabi", "Al Qubaisi", "Patel", "Sharma",
    "Williams", "Thompson", "Chen", "Kim", "Singh",
    "Fernandez", "Russo", "Müller", "Ahmed", "Hassan",
]
DUBAI_DATE_BIOS = [
    "Architect based in DIFC. I moved from Beirut six years ago and somehow stayed. I think it's the light — or maybe just inertia. Currently working on a project I'm not allowed to talk about.",
    "Finance. I know. I try to make up for it by being a genuinely good cook and asking interesting questions. Spent three years in London before Dubai. I prefer this.",
    "Pilot based in DXB. My schedule is strange, my appreciation for a good meal is not. I read a lot on layovers.",
    "I came to Dubai for a two-year contract and it turned into seven. Fashion buyer. I travel more than I should and cook more than people expect.",
    "Software engineer who works remotely and uses the flexibility well. Most weeks I'm in Dubai; occasionally somewhere else. I'm easygoing until I'm not.",
    "I run a small sustainable design studio out of Al Quoz. The work is good. The life balance is a project I'm still working on.",
    "Marketing director at a hospitality group. I've eaten at most of the restaurants here and have opinions. I'm also not insufferable about it — I just know what I like.",
    "I'm from Kerala, been in Dubai for four years. I work in product management and try to stay curious. I make very good chai and I'll prove it.",
    "Physiotherapist. I see a lot of people in pain and try to help. Outside of work I run along the Marina and spend Fridays cooking something that takes most of the day.",
    "I work in renewable energy — specifically solar projects across the GCC. I've always liked the idea of things that will outlast the people who built them.",
    "Journalist who ended up in Dubai covering tech and business. Good at listening, occasionally better at writing. I like people who say what they mean.",
    "I'm a UX designer at a fintech company. I think carefully about how things work and how they feel. That applies to most things.",
    "Ex-consultant, now building something in property tech. I left McKinsey two years ago and the gap between who I was there and who I want to be keeps closing.",
    "Based in Jumeirah. I do digital marketing but I'm trying not to make it my whole identity. I cook Italian better than most Italians I've met.",
    "Nurse at a private hospital. Twelve-hour shifts make you clear about what actually matters. I value time, honesty, and a really good meal.",
]
DUBAI_CITIES = [
    ("Dubai", "UAE"),
    ("Dubai, Downtown", "UAE"),
    ("Dubai, Marina", "UAE"),
    ("Dubai, JBR", "UAE"),
    ("Dubai, Business Bay", "UAE"),
    ("Dubai, DIFC", "UAE"),
    ("Dubai, Jumeirah", "UAE"),
    ("Dubai, Palm Jumeirah", "UAE"),
    ("Sharjah", "UAE"),
    ("Abu Dhabi", "UAE"),
    ("Abu Dhabi, Al Reem Island", "UAE"),
    ("Al Ain", "UAE"),
]

# ── Mumbai / India seed data ──────────────────────────────────────────────────
MUM_BASE_LAT   = 19.0760
MUM_BASE_LNG   = 72.8777
MUM_MAX_RADIUS = 50  # km — Mumbai metro area

MUMBAI_MALE_NAMES = [
    "Arjun", "Rohan", "Vikram", "Aarav", "Kabir",
    "Siddharth", "Aditya", "Rahul", "Dev", "Nikhil",
    "Ishaan", "Karan", "Varun", "Shreyas", "Armaan",
]
MUMBAI_FEMALE_NAMES = [
    "Ananya", "Priya", "Divya", "Nisha", "Anika",
    "Isha", "Shreya", "Pooja", "Meera", "Kavya",
    "Riya", "Tara", "Simran", "Aditi", "Sana",
]
MUMBAI_LAST_NAMES = [
    "Sharma", "Patel", "Mehta", "Joshi", "Kapoor",
    "Singh", "Verma", "Gupta", "Malhotra", "Nair",
    "Iyer", "Rao", "Desai", "Shah", "Shetty",
]
MUMBAI_DATE_BIOS = [
    "Architect. I grew up in Pune and came to Mumbai for work, which is exactly what everyone says. Five years later I've stopped counting. I'm working on an adaptive reuse project in Lower Parel that I care about more than I probably should.",
    "I'm a doctor at a hospital in Matunga. Long hours teach you that the moments outside work matter. I try to make them count — good food, honest conversations, early mornings before the city gets loud.",
    "IIM grad who spent two years in consulting before realising I wanted to build things instead of advise on them. Now in a Series B startup in Powai. The work is unpredictable. I like that.",
    "Fashion designer with a studio in Bandra. I work with Indian textiles and try to make things that are honest about where they come from. I'm better at making things than writing bios.",
    "I work in investment banking and try not to let it define me. I play cricket badly on Sunday mornings and read fiction in the evenings. Dosa is non-negotiable.",
    "Journalist. I write about tech and its impact on cities. Mumbai has given me more material than I can use. I listen more than I speak, which people find confusing at first.",
    "Software engineer at a company building payments infrastructure for tier-2 cities. I take long walks in Colaba on weekends and have strong feelings about South Mumbai.",
    "I studied film at FTII and came to Mumbai with big plans and adjusted expectations. I'm now doing documentary work I'm proud of and learning to be patient.",
    "I'm a product manager who spent three years in Singapore before moving back. Mumbai felt like the right place to be right now. Still figuring out whether I was right.",
    "Dancer and choreographer. I run a small studio in Andheri and teach on weekends. I've learned a lot about people from watching how they move.",
    "Biotech researcher. I work on diagnostics and spend a lot of time thinking about systems that most people never see. Outside the lab I'm significantly more relaxed.",
    "I work in wealth management and try to be useful to the people I work with. I'm from Gujarat, have lived in Mumbai for eight years, and still make khichdi when I need comfort.",
    "Content director at a media company. I've built a career out of knowing what to say and how — now I'm trying to get better at the quieter things.",
    "Trekking guide on weekends, data scientist during the week. The Sahyadris are 90 minutes from the city and most people in the city have never been.",
    "I'm a civil engineer working on Mumbai's metro extension. It's unglamorous, slow, and I think it matters. Looking for someone who values the long game.",
]
MUMBAI_CITIES = [
    ("Mumbai", "India"),
    ("Mumbai, Bandra", "India"),
    ("Mumbai, Andheri", "India"),
    ("Mumbai, Juhu", "India"),
    ("Mumbai, Colaba", "India"),
    ("Mumbai, Powai", "India"),
    ("Mumbai, Lower Parel", "India"),
    ("Navi Mumbai", "India"),
    ("Thane", "India"),
    ("Pune", "India"),
]

# ── Istanbul / Turkey seed data ───────────────────────────────────────────────
IST_BASE_LAT   = 41.0082
IST_BASE_LNG   = 28.9784
IST_MAX_RADIUS = 60  # km

ISTANBUL_MALE_NAMES = [
    "Mehmet", "Ahmet", "Mustafa", "Emre", "Burak",
    "Kerem", "Serkan", "Tolga", "Cem", "Berk",
    "Furkan", "Yusuf", "Osman", "Ibrahim", "Selim",
]
ISTANBUL_FEMALE_NAMES = [
    "Zeynep", "Elif", "Ayşe", "Fatma", "Merve",
    "Selin", "Büşra", "Derya", "Cansu", "Beren",
    "Naz", "İrem", "Tuğba", "Ceren", "Dilara",
]
ISTANBUL_LAST_NAMES = [
    "Yılmaz", "Kaya", "Demir", "Çelik", "Şahin",
    "Doğan", "Aydın", "Arslan", "Koç", "Kurt",
    "Özturk", "Polat", "Erdoğan", "Güneş", "Aslan",
]
ISTANBUL_DATE_BIOS = [
    "Architect. I work between Istanbul and Ankara, mostly on restoration projects. There's something about fixing what was already there that I find more interesting than building from scratch.",
    "I'm a doctor at a hospital in Şişli. I grew up in Ankara, moved here for residency, stayed for the city. Istanbul takes a while to make sense but then it does.",
    "Journalist. I cover politics and media. The job is exhausting and I wouldn't trade it. I unwind with long walks across the Bosphorus bridges and very good börek.",
    "Software engineer at a fintech company in Levent. I moved back from Berlin two years ago and rediscovered that Istanbul is its own thing — harder and better.",
    "History professor at Boğaziçi. I know things about this city that aren't in any guidebook. I'm good at turning that into an interesting evening, not a lecture.",
    "Film director. My third short just finished post-production. I'm in that uncertain gap between projects where everything looks possible and nothing is certain.",
    "I work in private equity and try to keep my identity separate from my work, with mixed results. I play tennis badly and cook well. Both are improving.",
    "Fashion designer with a studio in Nişantaşı. I work with local craft techniques and take it seriously. I'm better company than this bio suggests.",
    "Photographer. I shoot architecture and portraits. Istanbul gives me more than I can process, which is why I've been here eleven years.",
    "I'm a civil engineer on infrastructure projects. Not glamorous, but I'm building things that will still be here in a hundred years. That's the part I hold onto.",
    "I left Istanbul at 22 for London, came back at 30. The two versions of the city I know don't entirely overlap and I find that interesting.",
    "Translator — Turkish, English, French. I live in language and find it strange how much meaning gets lost and found again at every border.",
    "Tech investor. I see a lot of pitches and meet a lot of people. I'm trying to get better at the ones that aren't about work.",
    "Chef who trained in Lyon and came back to run a small restaurant in Karaköy. The menu changes every week. The tea list does not.",
    "I teach Turkish at a language school and write fiction on the side. The novel is slow. I'm okay with that.",
]
ISTANBUL_CITIES = [
    ("Istanbul", "Turkey"),
    ("Istanbul, Kadıköy", "Turkey"),
    ("Istanbul, Beşiktaş", "Turkey"),
    ("Istanbul, Beyoğlu", "Turkey"),
    ("Istanbul, Şişli", "Turkey"),
    ("Istanbul, Üsküdar", "Turkey"),
    ("Istanbul, Bakırköy", "Turkey"),
    ("Ankara", "Turkey"),
    ("İzmir", "Turkey"),
]


def make_dubai_date_profile(idx: int):
    is_male = idx % 2 == 0
    first_name = random.choice(DUBAI_MALE_NAMES if is_male else DUBAI_FEMALE_NAMES)
    last_name  = random.choice(DUBAI_LAST_NAMES)
    gender_id  = random.choice([223] if is_male else [224, 225])
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    photos = random.sample(photos_pool, random.randint(2, 4))
    lat, lng = random_coords_near(DXB_BASE_LAT, DXB_BASE_LNG, DXB_MAX_RADIUS)
    city_name, country = random.choice(DUBAI_CITIES)
    dob = random_dob(22, 38)
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
        "bio":                random.choice(DUBAI_DATE_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.68,
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
        "subscription_tier":  random.choices(["free", "pro"], weights=[60, 40])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[30, 70])[0],
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


def make_mumbai_date_profile(idx: int):
    is_male = idx % 2 == 0
    first_name = random.choice(MUMBAI_MALE_NAMES if is_male else MUMBAI_FEMALE_NAMES)
    last_name  = random.choice(MUMBAI_LAST_NAMES)
    gender_id  = random.choice([223] if is_male else [224, 225])
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    photos = random.sample(photos_pool, random.randint(2, 4))
    lat, lng = random_coords_near(MUM_BASE_LAT, MUM_BASE_LNG, MUM_MAX_RADIUS)
    city_name, country = random.choice(MUMBAI_CITIES)
    dob = random_dob(22, 35)
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
        "bio":                random.choice(MUMBAI_DATE_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.65,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 100)),
        "updated_at":         datetime.now(timezone.utc),
        "date_of_birth":      dob,
        "latitude":           lat,
        "longitude":          lng,
        "city":               city_name,
        "country":            country,
        "address":            f"{city_name}, {country}",
        "height_cm":          random.randint(152, 188),
        "gender_id":          gender_id,
        "education_level_id": random.choice(EDUCATION_IDS),
        "looking_for_id":     random.choice(LOOKING_FOR_IDS),
        "family_plans_id":    random.choice(FAMILY_PLANS_IDS),
        "have_kids_id":       random.choice(HAVE_KIDS_IDS),
        "star_sign_id":       random.choice(STAR_SIGN_IDS),
        "religion_id":        random.choice(RELIGION_IDS),
        "subscription_tier":  random.choices(["free", "pro"], weights=[70, 30])[0],
        "verification_status": random.choices(["unverified", "verified"], weights=[40, 60])[0],
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


def make_istanbul_date_profile(idx: int):
    is_male = idx % 2 == 0
    first_name = random.choice(ISTANBUL_MALE_NAMES if is_male else ISTANBUL_FEMALE_NAMES)
    last_name  = random.choice(ISTANBUL_LAST_NAMES)
    gender_id  = random.choice([223] if is_male else [224, 225])
    photos_pool = MALE_PHOTOS if is_male else FEMALE_PHOTOS
    photos = random.sample(photos_pool, random.randint(2, 4))
    lat, lng = random_coords_near(IST_BASE_LAT, IST_BASE_LNG, IST_MAX_RADIUS)
    city_name, country = random.choice(ISTANBUL_CITIES)
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
        "bio":                random.choice(ISTANBUL_DATE_BIOS),
        "is_active":          True,
        "is_verified":        random.random() < 0.65,
        "is_onboarded":       True,
        "created_at":         datetime.now(timezone.utc) - timedelta(days=random.randint(1, 150)),
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
    # Use random seed=None so every run produces unique UUIDs + fresh variation
    random.seed(None)
    date_profiles  = [make_date_profile(i) for i in range(25)]
    work_profiles  = [make_work_profile(i) for i in range(25)]
    ryd_date       = [make_riyadh_date_profile(i) for i in range(20)]
    ryd_work       = [make_riyadh_work_profile(i) for i in range(10)]
    us_date        = [make_us_date_profile(i) for i in range(15)]
    dxb_date       = [make_dubai_date_profile(i) for i in range(20)]
    mum_date       = [make_mumbai_date_profile(i) for i in range(20)]
    ist_date       = [make_istanbul_date_profile(i) for i in range(15)]
    all_profiles   = date_profiles + work_profiles + ryd_date + ryd_work + us_date + dxb_date + mum_date + ist_date

    print(f"Inserting {len(all_profiles)} profiles  "
          f"(25 London date + 25 London work + 20 Riyadh date + 10 Riyadh work "
          f"+ 15 US date + 20 Dubai date + 20 Mumbai date + 15 Istanbul date)…")

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
    print(f"   • 20 date profiles  (Riyadh)")
    print(f"   • 10 work profiles  (Riyadh)")
    print(f"   • 15 date profiles  (Western US)")
    print(f"   • 20 date profiles  (Dubai / UAE)")
    print(f"   • 20 date profiles  (Mumbai / India)")
    print(f"   • 15 date profiles  (Istanbul / Turkey)")
    print(f"   • ~30 mutual likes seeded (where likes table exists)")


if __name__ == "__main__":
    asyncio.run(seed())
