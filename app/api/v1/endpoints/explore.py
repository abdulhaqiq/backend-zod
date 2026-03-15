"""
Explore endpoint — returns events, quick meets, trending vibes and categories.
All data is seeded per city/country so the feed looks local and relevant.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/explore", tags=["explore"])

# ─── Static seed data per city ────────────────────────────────────────────────

_CITY_SEEDS: dict[str, dict[str, Any]] = {
    "riyadh": {
        "featured": [
            {
                "id": "f_ryd_1",
                "title": "Riyadh Night Bazaar",
                "sub": "Street food, music & local art at Al-Bujairi",
                "image": "https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=800&q=80",
                "members": 214, "tag": "Tonight", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/men/21.jpg",
                    "https://randomuser.me/api/portraits/women/31.jpg",
                    "https://randomuser.me/api/portraits/men/41.jpg",
                ],
            },
            {
                "id": "f_ryd_2",
                "title": "Corniche Coffee Walk",
                "sub": "Sunrise stroll along the Riyadh Corniche",
                "image": "https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=800&q=80",
                "members": 67, "tag": "Tomorrow 7AM", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/women/11.jpg",
                    "https://randomuser.me/api/portraits/men/51.jpg",
                ],
            },
            {
                "id": "f_ryd_3",
                "title": "Rooftop Stargazing",
                "sub": "Clear skies & telescope session — Al Faisaliyah",
                "image": "https://images.unsplash.com/photo-1454789548928-9efd52dc4031?w=800&q=80",
                "members": 44, "tag": "Saturday", "pro": True,
                "going": [
                    "https://randomuser.me/api/portraits/women/72.jpg",
                    "https://randomuser.me/api/portraits/men/62.jpg",
                ],
            },
        ],
        "quick_meets": [
            {"id": "q_ryd_1", "emoji": "☕", "title": "Coffee", "sub": "Cozy cafes nearby", "members": 18},
            {"id": "q_ryd_2", "emoji": "🌮", "title": "Kabsa dinner", "sub": "Group meal", "members": 11},
            {"id": "q_ryd_3", "emoji": "🏃", "title": "Morning run", "sub": "Al Salam Park", "members": 9},
            {"id": "q_ryd_4", "emoji": "🎮", "title": "Esports café", "sub": "LAN night", "members": 23},
            {"id": "q_ryd_5", "emoji": "🛍️", "title": "Mall hangout", "sub": "Kingdom Mall", "members": 7},
        ],
        "trending": [
            {"id": "t_ryd_1", "emoji": "🌙", "title": "Night Life", "members": 390, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1566737236500-c8ac43014a67?w=400&q=80"},
            {"id": "t_ryd_2", "emoji": "🍽️", "title": "Foodie Runs", "members": 278, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=400&q=80"},
            {"id": "t_ryd_3", "emoji": "🏋️", "title": "Gym Squad", "members": 155, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1534438327276-14e5300c3a48?w=400&q=80"},
            {"id": "t_ryd_4", "emoji": "🎭", "title": "Arts & Culture", "members": 88, "hot": False, "pro": True, "image": "https://images.unsplash.com/photo-1536924940846-227afb31e2a5?w=400&q=80"},
            {"id": "t_ryd_5", "emoji": "🌿", "title": "Wellness", "members": 120, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400&q=80"},
        ],
        "categories": [
            {"id": "c_ryd_1", "emoji": "🤝", "title": "Friend Groups", "sub": "Meet people in Riyadh", "members": 143, "pro": False, "image": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=600&q=80"},
            {"id": "c_ryd_2", "emoji": "📚", "title": "Study & Work", "sub": "Cowork sessions", "members": 61, "pro": True, "image": "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=600&q=80"},
            {"id": "c_ryd_3", "emoji": "☕", "title": "Coffee Dates", "sub": "Casual meets at cosy cafes", "members": 210, "pro": False, "image": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?w=600&q=80"},
            {"id": "c_ryd_4", "emoji": "🎮", "title": "Gaming & Tech", "sub": "LAN parties & hackathons", "members": 97, "pro": True, "image": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?w=600&q=80"},
            {"id": "c_ryd_5", "emoji": "🍜", "title": "Food & Dining", "sub": "Group dinners & street eats", "members": 189, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&q=80"},
            {"id": "c_ryd_6", "emoji": "🌍", "title": "Travel Mates", "sub": "Find travel buddies", "members": 76, "pro": True, "image": "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=600&q=80"},
        ],
    },
    "dubai": {
        "featured": [
            {
                "id": "f_dxb_1",
                "title": "Downtown Rooftop Party",
                "sub": "Skyline views & sunset vibes",
                "image": "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?w=800&q=80",
                "members": 312, "tag": "Tonight", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/women/44.jpg",
                    "https://randomuser.me/api/portraits/men/32.jpg",
                    "https://randomuser.me/api/portraits/women/68.jpg",
                ],
            },
            {
                "id": "f_dxb_2",
                "title": "JBR Beach Volleyball",
                "sub": "Casual games at Jumeirah Beach",
                "image": "https://images.unsplash.com/photo-1612872087720-bb876e2e67d1?w=800&q=80",
                "members": 48, "tag": "Saturday 4PM", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/men/11.jpg",
                    "https://randomuser.me/api/portraits/women/25.jpg",
                ],
            },
            {
                "id": "f_dxb_3",
                "title": "Yacht Sunrise Sail",
                "sub": "Private yacht with fresh breakfast",
                "image": "https://images.unsplash.com/photo-1567899378494-47b22a2ae96a?w=800&q=80",
                "members": 24, "tag": "Sunday 6AM", "pro": True,
                "going": [
                    "https://randomuser.me/api/portraits/women/72.jpg",
                    "https://randomuser.me/api/portraits/men/55.jpg",
                ],
            },
        ],
        "quick_meets": [
            {"id": "q_dxb_1", "emoji": "☕", "title": "Coffee", "sub": "Best cafes in DIFC", "members": 22},
            {"id": "q_dxb_2", "emoji": "🏖️", "title": "Beach day", "sub": "JBR or Kite Beach", "members": 15},
            {"id": "q_dxb_3", "emoji": "🏃", "title": "Run Club", "sub": "Creek morning jog", "members": 19},
            {"id": "q_dxb_4", "emoji": "🍕", "title": "Grab food", "sub": "Lunch or dinner", "members": 31},
            {"id": "q_dxb_5", "emoji": "🛍️", "title": "Mall tour", "sub": "Dubai Mall & shops", "members": 12},
        ],
        "trending": [
            {"id": "t_dxb_1", "emoji": "🌙", "title": "Night Life", "members": 540, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1566737236500-c8ac43014a67?w=400&q=80"},
            {"id": "t_dxb_2", "emoji": "🏖️", "title": "Beach Vibes", "members": 420, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=400&q=80"},
            {"id": "t_dxb_3", "emoji": "🎵", "title": "Live Music", "members": 310, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=400&q=80"},
            {"id": "t_dxb_4", "emoji": "🎭", "title": "Arts & Culture", "members": 95, "hot": False, "pro": True, "image": "https://images.unsplash.com/photo-1536924940846-227afb31e2a5?w=400&q=80"},
            {"id": "t_dxb_5", "emoji": "🌿", "title": "Wellness", "members": 180, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400&q=80"},
        ],
        "categories": [
            {"id": "c_dxb_1", "emoji": "🤝", "title": "Friend Groups", "sub": "Meet expats & locals", "members": 267, "pro": False, "image": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=600&q=80"},
            {"id": "c_dxb_2", "emoji": "📚", "title": "Study & Work", "sub": "DIFC co-work sessions", "members": 88, "pro": True, "image": "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=600&q=80"},
            {"id": "c_dxb_3", "emoji": "☕", "title": "Coffee Dates", "sub": "Specialty cafes in the city", "members": 198, "pro": False, "image": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?w=600&q=80"},
            {"id": "c_dxb_4", "emoji": "🎮", "title": "Gaming & Tech", "sub": "LAN cafes & hackathons", "members": 112, "pro": True, "image": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?w=600&q=80"},
            {"id": "c_dxb_5", "emoji": "🍜", "title": "Food & Dining", "sub": "Group dinners downtown", "members": 244, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&q=80"},
            {"id": "c_dxb_6", "emoji": "🌍", "title": "Travel Mates", "sub": "Explore the region together", "members": 134, "pro": True, "image": "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=600&q=80"},
        ],
    },
    "london": {
        "featured": [
            {
                "id": "f_lon_1",
                "title": "Shoreditch Street Art Walk",
                "sub": "Explore East London's iconic murals",
                "image": "https://images.unsplash.com/photo-1513635269975-59663e0ac1ad?w=800&q=80",
                "members": 88, "tag": "Saturday", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/women/34.jpg",
                    "https://randomuser.me/api/portraits/men/42.jpg",
                    "https://randomuser.me/api/portraits/women/58.jpg",
                ],
            },
            {
                "id": "f_lon_2",
                "title": "Thames Sunset Run",
                "sub": "5K along the riverbank with the crew",
                "image": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=800&q=80",
                "members": 56, "tag": "Sunday 7AM", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/men/21.jpg",
                    "https://randomuser.me/api/portraits/women/17.jpg",
                ],
            },
            {
                "id": "f_lon_3",
                "title": "Private Jazz Supper Club",
                "sub": "Intimate dinner with live jazz, Mayfair",
                "image": "https://images.unsplash.com/photo-1415886412858-2fe07d62b0e1?w=800&q=80",
                "members": 32, "tag": "Friday 8PM", "pro": True,
                "going": [
                    "https://randomuser.me/api/portraits/women/63.jpg",
                    "https://randomuser.me/api/portraits/men/74.jpg",
                ],
            },
        ],
        "quick_meets": [
            {"id": "q_lon_1", "emoji": "☕", "title": "Coffee", "sub": "Specialty coffee shops", "members": 28},
            {"id": "q_lon_2", "emoji": "🚶", "title": "Park walk", "sub": "Hyde Park stroll", "members": 14},
            {"id": "q_lon_3", "emoji": "🍺", "title": "Pub night", "sub": "Classic British pub", "members": 37},
            {"id": "q_lon_4", "emoji": "🎬", "title": "Cinema", "sub": "BFI or West End", "members": 11},
            {"id": "q_lon_5", "emoji": "🏃", "title": "Run Club", "sub": "Battersea Park jog", "members": 22},
        ],
        "trending": [
            {"id": "t_lon_1", "emoji": "🍺", "title": "Pub Crawls", "members": 480, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=400&q=80"},
            {"id": "t_lon_2", "emoji": "🎵", "title": "Live Music", "members": 352, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=400&q=80"},
            {"id": "t_lon_3", "emoji": "🏃", "title": "Run Club", "members": 290, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=400&q=80"},
            {"id": "t_lon_4", "emoji": "🎭", "title": "Theatre & Arts", "members": 120, "hot": False, "pro": True, "image": "https://images.unsplash.com/photo-1536924940846-227afb31e2a5?w=400&q=80"},
            {"id": "t_lon_5", "emoji": "🌿", "title": "Wellness", "members": 145, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400&q=80"},
        ],
        "categories": [
            {"id": "c_lon_1", "emoji": "🤝", "title": "Friend Groups", "sub": "Meet people in London", "members": 312, "pro": False, "image": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=600&q=80"},
            {"id": "c_lon_2", "emoji": "📚", "title": "Study & Work", "sub": "Cowork sessions", "members": 98, "pro": True, "image": "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=600&q=80"},
            {"id": "c_lon_3", "emoji": "☕", "title": "Coffee Dates", "sub": "Flat whites & good chat", "members": 240, "pro": False, "image": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?w=600&q=80"},
            {"id": "c_lon_4", "emoji": "🎮", "title": "Gaming & Tech", "sub": "LAN & hackathons", "members": 76, "pro": True, "image": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?w=600&q=80"},
            {"id": "c_lon_5", "emoji": "🍜", "title": "Food & Dining", "sub": "London's best restaurants", "members": 198, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&q=80"},
            {"id": "c_lon_6", "emoji": "🌍", "title": "Travel Mates", "sub": "Weekend trips from London", "members": 110, "pro": True, "image": "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=600&q=80"},
        ],
    },
    "mumbai": {
        "featured": [
            {
                "id": "f_mum_1",
                "title": "Marine Drive Sunset Walk",
                "sub": "Golden hour stroll along the Queen's Necklace",
                "image": "https://images.unsplash.com/photo-1529253355930-ddbe423a2ac7?w=800&q=80",
                "members": 178, "tag": "Tonight 6PM", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/women/14.jpg",
                    "https://randomuser.me/api/portraits/men/18.jpg",
                    "https://randomuser.me/api/portraits/women/22.jpg",
                ],
            },
            {
                "id": "f_mum_2",
                "title": "Bandra Café Hop",
                "sub": "Best third-wave coffee in West Bandra",
                "image": "https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=800&q=80",
                "members": 43, "tag": "Sunday 10AM", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/men/33.jpg",
                    "https://randomuser.me/api/portraits/women/29.jpg",
                ],
            },
            {
                "id": "f_mum_3",
                "title": "Dharavi Art Night",
                "sub": "Local artist studios & street food tour",
                "image": "https://images.unsplash.com/photo-1472653525502-fc569e405a74?w=800&q=80",
                "members": 62, "tag": "Saturday", "pro": True,
                "going": [
                    "https://randomuser.me/api/portraits/men/47.jpg",
                    "https://randomuser.me/api/portraits/women/51.jpg",
                ],
            },
        ],
        "quick_meets": [
            {"id": "q_mum_1", "emoji": "☕", "title": "Chai & chat", "sub": "Local tapri near you", "members": 24},
            {"id": "q_mum_2", "emoji": "🚶", "title": "Beach walk", "sub": "Juhu or Versova", "members": 16},
            {"id": "q_mum_3", "emoji": "🍛", "title": "Thali lunch", "sub": "Authentic Maharashtrian", "members": 19},
            {"id": "q_mum_4", "emoji": "🎬", "title": "Bollywood night", "sub": "PVR or INOX screening", "members": 13},
            {"id": "q_mum_5", "emoji": "🏃", "title": "Run Club", "sub": "BKC or Marine Drive", "members": 27},
        ],
        "trending": [
            {"id": "t_mum_1", "emoji": "🎵", "title": "Live Music", "members": 410, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=400&q=80"},
            {"id": "t_mum_2", "emoji": "🍛", "title": "Foodie Runs", "members": 360, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=400&q=80"},
            {"id": "t_mum_3", "emoji": "🌙", "title": "Night Life", "members": 290, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1566737236500-c8ac43014a67?w=400&q=80"},
            {"id": "t_mum_4", "emoji": "🎭", "title": "Arts & Culture", "members": 110, "hot": False, "pro": True, "image": "https://images.unsplash.com/photo-1536924940846-227afb31e2a5?w=400&q=80"},
            {"id": "t_mum_5", "emoji": "🌿", "title": "Wellness", "members": 95, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400&q=80"},
        ],
        "categories": [
            {"id": "c_mum_1", "emoji": "🤝", "title": "Friend Groups", "sub": "Make new friends in Mumbai", "members": 223, "pro": False, "image": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=600&q=80"},
            {"id": "c_mum_2", "emoji": "📚", "title": "Study & Work", "sub": "BKC cowork sessions", "members": 72, "pro": True, "image": "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=600&q=80"},
            {"id": "c_mum_3", "emoji": "☕", "title": "Coffee Dates", "sub": "Best cafes in Bandra", "members": 187, "pro": False, "image": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?w=600&q=80"},
            {"id": "c_mum_4", "emoji": "🎮", "title": "Gaming & Tech", "sub": "LAN nights & meetups", "members": 84, "pro": True, "image": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?w=600&q=80"},
            {"id": "c_mum_5", "emoji": "🍜", "title": "Food & Dining", "sub": "Street food & restaurants", "members": 256, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&q=80"},
            {"id": "c_mum_6", "emoji": "🌍", "title": "Travel Mates", "sub": "Weekend trips from Mumbai", "members": 91, "pro": True, "image": "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=600&q=80"},
        ],
    },
    "new york": {
        "featured": [
            {
                "id": "f_nyc_1",
                "title": "Rooftop Bar Crawl",
                "sub": "Hit 5 rooftop bars in Manhattan",
                "image": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=800&q=80",
                "members": 287, "tag": "Tonight", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/women/44.jpg",
                    "https://randomuser.me/api/portraits/men/32.jpg",
                    "https://randomuser.me/api/portraits/women/68.jpg",
                ],
            },
            {
                "id": "f_nyc_2",
                "title": "Central Park Picnic",
                "sub": "BYO blanket — we'll handle the playlist",
                "image": "https://images.unsplash.com/photo-1534430480872-3498386e7856?w=800&q=80",
                "members": 74, "tag": "Saturday 12PM", "pro": False,
                "going": [
                    "https://randomuser.me/api/portraits/men/11.jpg",
                    "https://randomuser.me/api/portraits/women/25.jpg",
                ],
            },
        ],
        "quick_meets": [
            {"id": "q_nyc_1", "emoji": "☕", "title": "Coffee", "sub": "Brooklyn roasters", "members": 34},
            {"id": "q_nyc_2", "emoji": "🚶", "title": "City walk", "sub": "Brooklyn Bridge stroll", "members": 21},
            {"id": "q_nyc_3", "emoji": "🍕", "title": "Pizza lunch", "sub": "Best slice in NYC", "members": 29},
            {"id": "q_nyc_4", "emoji": "🎬", "title": "Movie night", "sub": "Indie cinema in Brooklyn", "members": 15},
            {"id": "q_nyc_5", "emoji": "🏃", "title": "Run Club", "sub": "Hudson River Greenway", "members": 42},
        ],
        "trending": [
            {"id": "t_nyc_1", "emoji": "🌙", "title": "Night Life", "members": 620, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1566737236500-c8ac43014a67?w=400&q=80"},
            {"id": "t_nyc_2", "emoji": "🎵", "title": "Live Music", "members": 480, "hot": True, "pro": False, "image": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=400&q=80"},
            {"id": "t_nyc_3", "emoji": "🏃", "title": "Run Club", "members": 340, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=400&q=80"},
            {"id": "t_nyc_4", "emoji": "🎭", "title": "Arts & Culture", "members": 210, "hot": False, "pro": True, "image": "https://images.unsplash.com/photo-1536924940846-227afb31e2a5?w=400&q=80"},
            {"id": "t_nyc_5", "emoji": "🌿", "title": "Wellness", "members": 175, "hot": False, "pro": False, "image": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400&q=80"},
        ],
        "categories": [
            {"id": "c_nyc_1", "emoji": "🤝", "title": "Friend Groups", "sub": "Meet people across NYC", "members": 456, "pro": False, "image": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=600&q=80"},
            {"id": "c_nyc_2", "emoji": "📚", "title": "Study & Work", "sub": "WeWork & cowork spots", "members": 134, "pro": True, "image": "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?w=600&q=80"},
            {"id": "c_nyc_3", "emoji": "☕", "title": "Coffee Dates", "sub": "Cozy NYC coffee shops", "members": 312, "pro": False, "image": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?w=600&q=80"},
            {"id": "c_nyc_4", "emoji": "🎮", "title": "Gaming & Tech", "sub": "LAN cafes & meetups", "members": 143, "pro": True, "image": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?w=600&q=80"},
            {"id": "c_nyc_5", "emoji": "🍜", "title": "Food & Dining", "sub": "World's best restaurant scene", "members": 389, "pro": False, "image": "https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&q=80"},
            {"id": "c_nyc_6", "emoji": "🌍", "title": "Travel Mates", "sub": "Weekend trips from NYC", "members": 167, "pro": True, "image": "https://images.unsplash.com/photo-1501854140801-50d01698950b?w=600&q=80"},
        ],
    },
}

# Default fallback data (used when city not recognised)
_DEFAULT_SEED = _CITY_SEEDS["new york"]


def _get_seed(city: str | None, country: str | None) -> dict[str, Any]:
    """Return the best seed data for the given city/country."""
    key = (city or "").lower().strip()
    if key in _CITY_SEEDS:
        return _CITY_SEEDS[key]
    # Try country-based fallback
    country_key = (country or "").lower().strip()
    if "india" in country_key:
        return _CITY_SEEDS["mumbai"]
    if "saudi" in country_key or "ksa" in country_key:
        return _CITY_SEEDS["riyadh"]
    if "uae" in country_key or "emirates" in country_key:
        return _CITY_SEEDS["dubai"]
    if "uk" in country_key or "united kingdom" in country_key or "britain" in country_key:
        return _CITY_SEEDS["london"]
    return _DEFAULT_SEED


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@router.get("/feed", summary="Get explore feed for current user's city")
async def get_explore_feed(
    city: str | None = Query(None, description="Override city (optional)"),
    country: str | None = Query(None, description="Override country (optional)"),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    resolved_city = city or current_user.city
    resolved_country = country or current_user.country
    seed = _get_seed(resolved_city, resolved_country)
    return {
        "city": resolved_city or "Nearby",
        "country": resolved_country,
        "featured": seed["featured"],
        "quick_meets": seed["quick_meets"],
        "trending": seed["trending"],
        "categories": seed["categories"],
    }
