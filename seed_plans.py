"""
Seed Zod Pro and Premium+ subscription plans (3 billing periods each).

Features are stored as structured JSONB objects so the backend can
read actual limits (e.g. super_likes.limit = 5) at runtime.

Feature types:
  bool     — {"key": "...", "type": "bool",     "value": true/false}
  quantity — {"key": "...", "type": "quantity", "limit": N, "period": "weekly|monthly", "display": "N / week"}
  label    — {"key": "...", "type": "label",    "display": "Standard|Priority|Advanced|2×"}

Run from the backend/ directory:
    python3 seed_plans.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(__file__))
from app.core.config import settings  # noqa: E402

DB_URL = settings.DATABASE_URL

# ─── Structured feature definitions ──────────────────────────────────────────

BASE_BOOL = [
    {"key": "unlimited_likes",   "label": "Unlimited likes",   "icon": "heart",          "type": "bool", "value": True},
    {"key": "see_who_liked_you", "label": "See who liked you", "icon": "eye",            "type": "bool", "value": True},
    {"key": "rewind",            "label": "Rewind last swipe", "icon": "refresh-circle", "type": "bool", "value": True},
    {"key": "advanced_filters",  "label": "Advanced filters",  "icon": "options",        "type": "bool", "value": True},
    {"key": "travel_mode",       "label": "Travel Mode",       "icon": "airplane",       "type": "bool", "value": True},
    {"key": "no_ads",            "label": "No ads",            "icon": "ban",            "type": "bool", "value": True},
]

PRO_FEATURES = [
    *BASE_BOOL,
    {"key": "super_likes",         "label": "Super Likes",        "icon": "star",             "type": "quantity", "limit": 5,  "period": "weekly",  "display": "5 / week"},
    {"key": "profile_boosts",      "label": "Profile Boosts",     "icon": "rocket",           "type": "quantity", "limit": 1,  "period": "monthly", "display": "1 / month"},
    {"key": "ai_smart_matching",   "label": "AI Smart Matching",  "icon": "sparkles",         "type": "label",    "display": "Standard"},
    {"key": "priority_visibility", "label": "Priority visibility","icon": "trending-up",      "type": "label",    "display": "Standard"},
    {"key": "read_receipts",       "label": "Read receipts",      "icon": "chatbubble",       "type": "bool",     "value": False},
    {"key": "incognito",           "label": "Incognito browsing", "icon": "eye-off",          "type": "bool",     "value": False},
    {"key": "vip_support",         "label": "VIP support",        "icon": "shield-checkmark", "type": "bool",     "value": False},
]

PREMIUM_PLUS_FEATURES = [
    *BASE_BOOL,
    {"key": "super_likes",         "label": "Super Likes",        "icon": "star",             "type": "quantity", "limit": 10, "period": "weekly",  "display": "10 / week"},
    {"key": "profile_boosts",      "label": "Profile Boosts",     "icon": "rocket",           "type": "quantity", "limit": 2,  "period": "monthly", "display": "2 / month"},
    {"key": "ai_smart_matching",   "label": "AI Smart Matching",  "icon": "sparkles",         "type": "label",    "display": "Priority"},
    {"key": "priority_visibility", "label": "Priority visibility","icon": "trending-up",      "type": "label",    "display": "2×"},
    {"key": "read_receipts",       "label": "Read receipts",      "icon": "chatbubble",       "type": "bool",     "value": True},
    {"key": "incognito",           "label": "Incognito browsing", "icon": "eye-off",          "type": "bool",     "value": True},
    {"key": "vip_support",         "label": "VIP support",        "icon": "shield-checkmark", "type": "bool",     "value": True},
]

# ─── Plan definitions ─────────────────────────────────────────────────────────

PLANS = [
    # ── Pro ──────────────────────────────────────────────────────────────────
    {
        "apple_product_id": "028399823293",
        "name": "Pro 3 Months",
        "interval": "threemonth",
        "price_usd": 34.99,
        "price_display": "$11.67/mo",
        "badge": "Best Value",
        "description": "Billed $34.99 every 3 months · Save 22%",
        "features": PRO_FEATURES,
        "sort_order": 0,
    },
    {
        "apple_product_id": "293423874982734",
        "name": "Pro Monthly",
        "interval": "monthly",
        "price_usd": 14.99,
        "price_display": "$14.99/mo",
        "badge": "Popular",
        "description": "Billed monthly, cancel anytime",
        "features": PRO_FEATURES,
        "sort_order": 1,
    },
    {
        "apple_product_id": "928392834923",
        "name": "Pro Weekly",
        "interval": "weekly",
        "price_usd": 4.99,
        "price_display": "$4.99/wk",
        "badge": None,
        "description": "Billed weekly, cancel anytime",
        "features": PRO_FEATURES,
        "sort_order": 2,
    },
    # ── Premium+ ─────────────────────────────────────────────────────────────
    {
        "apple_product_id": "289392323",
        "name": "Premium+ 3 Months",
        "interval": "threemonth",
        "price_usd": 54.99,
        "price_display": "$18.33/mo",
        "badge": "Best Value",
        "description": "Billed $54.99 every 3 months · Save 27%",
        "features": PREMIUM_PLUS_FEATURES,
        "sort_order": 3,
    },
    {
        "apple_product_id": "823823023",
        "name": "Premium+ Monthly",
        "interval": "monthly",
        "price_usd": 24.99,
        "price_display": "$24.99/mo",
        "badge": "Popular",
        "description": "Billed monthly, cancel anytime",
        "features": PREMIUM_PLUS_FEATURES,
        "sort_order": 4,
    },
    {
        "apple_product_id": "2038923892",
        "name": "Premium+ Weekly",
        "interval": "weekly",
        "price_usd": 9.99,
        "price_display": "$9.99/wk",
        "badge": None,
        "description": "Billed weekly, cancel anytime",
        "features": PREMIUM_PLUS_FEATURES,
        "sort_order": 5,
    },
]

# ─── Seed ─────────────────────────────────────────────────────────────────────

async def seed() -> None:
    engine = create_async_engine(DB_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        for plan in PLANS:
            await session.execute(
                text("""
                    INSERT INTO subscription_plans
                        (id, name, apple_product_id, interval, price_display,
                         price_usd, badge, description, features, sort_order, is_active,
                         created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), :name, :apple_product_id, :interval,
                         :price_display, :price_usd, :badge, :description,
                         CAST(:features AS jsonb), :sort_order, true,
                         now(), now())
                    ON CONFLICT (apple_product_id) DO UPDATE SET
                        name          = EXCLUDED.name,
                        interval      = EXCLUDED.interval,
                        price_display = EXCLUDED.price_display,
                        price_usd     = EXCLUDED.price_usd,
                        badge         = EXCLUDED.badge,
                        description   = EXCLUDED.description,
                        features      = EXCLUDED.features,
                        sort_order    = EXCLUDED.sort_order,
                        is_active     = true,
                        updated_at    = now()
                """),
                {
                    "name":             plan["name"],
                    "apple_product_id": plan["apple_product_id"],
                    "interval":         plan["interval"],
                    "price_display":    plan["price_display"],
                    "price_usd":        plan["price_usd"],
                    "badge":            plan["badge"],
                    "description":      plan["description"],
                    "features":         json.dumps(plan["features"]),
                    "sort_order":       plan["sort_order"],
                },
            )

        await session.commit()

    await engine.dispose()

    print("✅  Seeded subscription plans:")
    for p in PLANS:
        badge = f"  [{p['badge']}]" if p["badge"] else ""
        tier  = "Premium+" if "Premium+" in p["name"] else "Pro      "
        sl    = next((f["limit"] for f in p["features"] if f["key"] == "super_likes"), "?")
        print(f"    • {tier}  {p['name']:<26}  {p['price_display']:<12}  SL:{sl}/wk  {p['apple_product_id']}{badge}")


if __name__ == "__main__":
    asyncio.run(seed())
