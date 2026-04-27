from contextlib import asynccontextmanager
import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import Dict, Tuple

from asyncpg.exceptions import ConnectionDoesNotExistError, TooManyConnectionsError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy.exc import DBAPIError

_main_log = logging.getLogger(__name__)

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.security import decode_access_token
from app.db.base import Base
from app.db.session import engine, AsyncSessionLocal

# Import all models so Base.metadata knows about every table
import app.models.user  # noqa: F401
import app.models.otp  # noqa: F401
import app.models.refresh_token  # noqa: F401
import app.models.pickup_line  # noqa: F401
import app.models.subscription_plan  # noqa: F401
import app.models.user_score  # noqa: F401
import app.models.user_compatibility  # noqa: F401
import app.models.ai_credits_transaction  # noqa: F401
import app.models.gift_card  # noqa: F401
import app.models.user_report  # noqa: F401
import app.models.message  # noqa: F401
import app.models.message_reaction  # noqa: F401
import app.models.tod_round  # noqa: F401
import app.models.verification  # noqa: F401
import app.models.lookup  # noqa: F401
import app.models.card  # noqa: F401
import app.models.mini_game  # noqa: F401
import app.models.marketing  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup (no-op for existing tables).
    # Wrapped in try/except so a transient DB timeout doesn't prevent startup
    # when tables already exist (e.g. connecting to a remote production DB).
    # create_all is intentionally skipped — all schema changes are handled
    # by the incremental _MIGRATIONS list below which use IF NOT EXISTS clauses.
    # Running create_all on every startup against a remote SSL DB is slow (10-15s)
    # and blocks the server from accepting connections during that window.

    # Incremental column migrations — all run in a single connection to avoid the
    # SSL handshake overhead of opening a new connection per statement (which caused
    # 8-15s startup timeouts). Each statement uses IF NOT EXISTS so it's idempotent.
    from sqlalchemy import text as _text
    import logging as _mlog
    _mig_log = _mlog.getLogger(__name__)
    _MIGRATIONS = [
        "ALTER TABLE user_scores ADD COLUMN IF NOT EXISTS profile_hash VARCHAR(32)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS id_scan_required BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS travel_expires_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_latitude DOUBLE PRECISION",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_longitude DOUBLE PRECISION",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_city VARCHAR(128)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_country VARCHAR(128)",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sect_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS prayer_frequency_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS marriage_timeline_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wali_email VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wali_verified BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS blur_photos_halal BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS halal_mode_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_mode_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_sect JSONB",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_prayer_frequency JSONB",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_marriage_timeline JSONB",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_wali_verified_only BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_wants_to_work BOOLEAN",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS bypass_location_filter BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_new_match BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_new_message BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_super_like BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_liked_profile BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_profile_views BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_ai_picks BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_promotions BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notif_dating_tips BOOLEAN NOT NULL DEFAULT TRUE",
        # user_blocks table — stores mutual block relationships for feed exclusion
        """CREATE TABLE IF NOT EXISTS user_blocks (
            blocker_id UUID NOT NULL,
            blocked_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (blocker_id, blocked_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_user_blocks_blocker ON user_blocks (blocker_id)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_credits_balance INTEGER NOT NULL DEFAULT 0",
        # subscriptions table — full event history for every purchase, renewal, cancellation
        """CREATE TABLE IF NOT EXISTS subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_name VARCHAR(128),
            apple_product_id VARCHAR(256),
            tier VARCHAR(32) NOT NULL,
            interval VARCHAR(32),
            price_usd NUMERIC(10,2),
            revenuecat_customer_id VARCHAR(256),
            event_type VARCHAR(64) NOT NULL,
            starts_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_rc_customer ON subscriptions(revenuecat_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_created ON subscriptions(created_at DESC)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_credits_reset_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS linkedin_import_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS linkedin_import_reset_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_headline VARCHAR(256)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_persona VARCHAR(32)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_num_founders_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_primary_role_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_years_experience_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS work_job_search_status_id INTEGER",
        # ── Seed new work lookup categories (idempotent) ──────────────────────
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM lookup_options WHERE category = 'work_role') THEN
            INSERT INTO lookup_options (category, subcategory, emoji, label, sort_order, is_active) VALUES
              ('work_role','Leadership','🚀','Founder / Co-Founder',0,true),
              ('work_role','Leadership','👑','CEO',1,true),
              ('work_role','Leadership','🔧','CTO',2,true),
              ('work_role','Leadership','⚙️','COO',3,true),
              ('work_role','Leadership','💰','CFO',4,true),
              ('work_role','Leadership','🎯','CPO – Chief Product Officer',5,true),
              ('work_role','Leadership','📣','CMO – Chief Marketing Officer',6,true),
              ('work_role','Leadership','📈','CRO – Chief Revenue Officer',7,true),
              ('work_role','Engineering','💻','Software Engineer – Frontend',10,true),
              ('work_role','Engineering','🖥️','Software Engineer – Backend',11,true),
              ('work_role','Engineering','🔄','Software Engineer – Full Stack',12,true),
              ('work_role','Engineering','📱','Mobile Engineer',13,true),
              ('work_role','Engineering','🔧','Hardware Engineer',14,true),
              ('work_role','Engineering','🌐','Network Engineer',15,true),
              ('work_role','Engineering','☁️','DevOps / Infrastructure',16,true),
              ('work_role','Engineering','🤖','AI / ML Engineer',17,true),
              ('work_role','Engineering','🔒','Security Engineer',18,true),
              ('work_role','Engineering','📊','Data Engineer',19,true),
              ('work_role','Product','🎯','Product Manager',20,true),
              ('work_role','Product','🎨','Product Designer',21,true),
              ('work_role','Product','📊','Product Analyst',22,true),
              ('work_role','Design','🎨','UX Designer',30,true),
              ('work_role','Design','✏️','UI Designer',31,true),
              ('work_role','Design','🖼️','Brand / Visual Designer',32,true),
              ('work_role','Sales','📞','SDR / BDR',40,true),
              ('work_role','Sales','💼','Account Executive',41,true),
              ('work_role','Sales','🏆','Sales Manager',42,true),
              ('work_role','Sales','👔','VP Sales / Head of Sales',43,true),
              ('work_role','Marketing','📈','Growth Marketing',50,true),
              ('work_role','Marketing','✍️','Content Marketing',51,true),
              ('work_role','Marketing','📣','Brand Marketing',52,true),
              ('work_role','Marketing','📊','Performance Marketing',53,true),
              ('work_role','Marketing','🌐','SEO Specialist',54,true),
              ('work_role','Marketing','👥','Community Manager',55,true),
              ('work_role','Customer','🤝','Customer Success Manager',60,true),
              ('work_role','Customer','📞','Customer Support Lead',61,true),
              ('work_role','Customer','🔑','Account Manager',62,true),
              ('work_role','Operations','⚙️','Operations Manager',70,true),
              ('work_role','Operations','📋','Chief of Staff',71,true),
              ('work_role','Operations','🗂️','Project Manager',72,true),
              ('work_role','Finance','💹','Finance Manager',80,true),
              ('work_role','Finance','📊','Financial Analyst',81,true),
              ('work_role','Finance','🧾','Accounting',82,true),
              ('work_role','Data','📊','Data Analyst',90,true),
              ('work_role','Data','🔬','Data Scientist',91,true),
              ('work_role','Data','📈','BI Analyst',92,true),
              ('work_role','People & HR','👥','HR Manager',100,true),
              ('work_role','People & HR','🔍','Recruiter',101,true),
              ('work_role','People & HR','💚','People Ops Manager',102,true),
              ('work_role','Business Dev','🤝','Business Development Manager',110,true),
              ('work_role','Business Dev','🤲','Partnerships Manager',111,true);
          END IF;
        END $$
        """,
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM lookup_options WHERE category = 'work_num_founders') THEN
            INSERT INTO lookup_options (category, emoji, label, sort_order, is_active) VALUES
              ('work_num_founders','1️⃣','Solo Founder',0,true),
              ('work_num_founders','2️⃣','2 Founders',1,true),
              ('work_num_founders','3️⃣','3 Founders',2,true),
              ('work_num_founders','4️⃣','4+ Founders',3,true);
          END IF;
        END $$
        """,
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM lookup_options WHERE category = 'work_years_experience') THEN
            INSERT INTO lookup_options (category, emoji, label, sort_order, is_active) VALUES
              ('work_years_experience','🌱','Less than 1 year',0,true),
              ('work_years_experience','📅','1–2 years',1,true),
              ('work_years_experience','📆','3–5 years',2,true),
              ('work_years_experience','🔥','6–10 years',3,true),
              ('work_years_experience','⭐','10+ years',4,true);
          END IF;
        END $$
        """,
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM lookup_options WHERE category = 'work_job_search_status') THEN
            INSERT INTO lookup_options (category, emoji, label, sort_order, is_active) VALUES
              ('work_job_search_status','🔍','Actively looking',0,true),
              ('work_job_search_status','🌟','Open to opportunities',1,true),
              ('work_job_search_status','✋','Not looking right now',2,true);
          END IF;
        END $$
        """,
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trust_score INTEGER NOT NULL DEFAULT 10",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS device_blacklisted BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_religions JSONB",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
        # ── University email verification columns ──────────────────────────────
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS university VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS university_email VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS university_email_verified BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS university_otp_hash VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS university_otp_expires_at TIMESTAMPTZ",
        # Wali information fields
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wali_name VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wali_age INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wali_relation VARCHAR(128)",
        # is_banned — separates admin bans from snooze (is_active=False)
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE",
        # ── Marketing notification tables ──────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS marketing_countries (
            id SERIAL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            code VARCHAR(8) NOT NULL,
            region VARCHAR(64) NOT NULL,
            tz_name VARCHAR(64) NOT NULL,
            peak_hours JSONB NOT NULL DEFAULT '[]',
            primary_language VARCHAR(8) NOT NULL DEFAULT 'en',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            CONSTRAINT uq_marketing_country_code_tz UNIQUE (code, tz_name)
        )""",
        # User-level marketing notification tracking (prevents duplicate sends)
        """CREATE TABLE IF NOT EXISTS user_marketing_sends (
            user_id UUID NOT NULL,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            campaign_id INTEGER,
            PRIMARY KEY (user_id, sent_at)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_user_marketing_sends_user ON user_marketing_sends (user_id, sent_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_marketing_countries_region ON marketing_countries (region)",
        "CREATE INDEX IF NOT EXISTS idx_marketing_countries_code ON marketing_countries (code)",
        """CREATE TABLE IF NOT EXISTS marketing_templates (
            id SERIAL PRIMARY KEY,
            name VARCHAR(256) NOT NULL,
            language_code VARCHAR(8) NOT NULL DEFAULT 'en',
            title VARCHAR(256) NOT NULL,
            body TEXT NOT NULL,
            notif_type VARCHAR(32) NOT NULL DEFAULT 'promotions',
            data JSONB,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_marketing_templates_lang ON marketing_templates (language_code)",
        """CREATE TABLE IF NOT EXISTS marketing_campaigns (
            id SERIAL PRIMARY KEY,
            name VARCHAR(256),
            template_id INTEGER,
            custom_title VARCHAR(256),
            custom_body TEXT,
            target VARCHAR(32) NOT NULL DEFAULT 'all',
            target_value VARCHAR(128),
            language_code VARCHAR(8),
            scheduler_tz VARCHAR(64),
            scheduler_hour INTEGER,
            status VARCHAR(32) NOT NULL DEFAULT 'sent',
            triggered_by VARCHAR(32) NOT NULL DEFAULT 'admin',
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            sent_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_marketing_campaigns_created ON marketing_campaigns (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_marketing_campaigns_triggered ON marketing_campaigns (triggered_by)",
    ]
    import asyncio as _asyncio

    async def _run_migrations_bg() -> None:
        """
        Run schema migrations in the background after the server starts accepting
        requests. Uses a single DB connection for all statements (avoids the
        SSL handshake overhead that caused 8-15s startup delays when opening one
        connection per statement). All statements are IF NOT EXISTS so they are
        safe to run repeatedly.
        """
        await _asyncio.sleep(0)  # yield so lifespan finishes first
        try:
            async with engine.connect() as _conn:
                for _sql in _MIGRATIONS:
                    try:
                        await _conn.execute(_text(_sql))
                    except Exception as _migration_exc:
                        _mig_log.warning(
                            "Migration skipped (%s): %.120s",
                            _migration_exc.__class__.__name__, _sql,
                        )
                await _conn.commit()
            _mig_log.info("Background migrations complete.")
        except Exception as _conn_exc:
            _mig_log.warning(
                "Background migrations failed — DB unreachable: %s",
                _conn_exc.__class__.__name__,
            )

    import asyncio
    import logging
    _log = logging.getLogger(__name__)

    # Suppress "Future exception was never retrieved" for transient DNS/OS errors
    # that occur inside asyncpg's internal connection pool background tasks.
    _loop = asyncio.get_event_loop()
    _orig_handler = _loop.get_exception_handler() or _loop.default_exception_handler
    def _quiet_future_handler(loop, context):
        exc = context.get("exception")
        if isinstance(exc, OSError):
            return  # DNS failures / network transients — ignore silently
        _orig_handler(context) if callable(_orig_handler) else loop.default_exception_handler(context)
    _loop.set_exception_handler(_quiet_future_handler)

    # On restart: any attempt stuck in "pending" means the server was reloaded
    # mid-verification. Mark them as rejected so users aren't stuck forever.
    async def _recover_stale_attempts():
        from sqlalchemy import select, update
        from app.db.session import AsyncSessionLocal
        from app.models.verification import VerificationAttempt
        from app.models.user import User
        from datetime import datetime, timezone, timedelta

        try:
            async with AsyncSessionLocal() as db:
                # Find attempts pending for more than 5 minutes (server must have crashed)
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
                stale = (await db.execute(
                    select(VerificationAttempt)
                    .where(VerificationAttempt.status == "pending")
                    .where(VerificationAttempt.submitted_at < cutoff)
                )).scalars().all()

                for attempt in stale:
                    attempt.status = "rejected"
                    attempt.rejection_reason = "Verification interrupted by server restart. Please try again."
                    attempt.processed_at = datetime.now(timezone.utc)
                    # Also reset user status
                    user = await db.get(User, attempt.user_id)
                    if user and user.verification_status == "pending":
                        user.verification_status = "rejected"

                if stale:
                    await db.commit()
                    _log.info("Recovered %d stale pending attempt(s) on startup", len(stale))
        except Exception as exc:
            _log.warning("Stale attempt recovery failed (non-critical): %s", exc)

    async def _expire_travel_modes():
        """Hourly loop: reset travel mode for users whose 7-day window has elapsed."""
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models.user import User
        from datetime import datetime, timezone

        while True:
            await asyncio.sleep(3600)  # check every hour
            try:
                async with AsyncSessionLocal() as db:
                    now = datetime.now(timezone.utc)
                    result = await db.execute(
                        select(User).where(
                            User.travel_mode_enabled.is_(True),
                            User.travel_expires_at.isnot(None),
                            User.travel_expires_at <= now,
                        )
                    )
                    expired = result.scalars().all()
                    for user in expired:
                        user.travel_mode_enabled = False
                        user.travel_city = None
                        user.travel_country = None
                        user.travel_expires_at = None
                        # Restore the real GPS coordinates saved before travel mode
                        if user.real_latitude is not None:
                            user.latitude = user.real_latitude
                            user.longitude = user.real_longitude
                            user.city = user.real_city
                            user.country = user.real_country
                        user.real_latitude = None
                        user.real_longitude = None
                        user.real_city = None
                        user.real_country = None
                    if expired:
                        await db.commit()
                        _log.info("Travel mode expired and reset for %d user(s)", len(expired))
            except Exception as exc:
                _log.warning("Travel mode expiry loop error (non-critical): %s", exc)

    async def _expire_subscriptions():
        """
        Every 15 minutes: downgrade users whose subscription_expires_at has passed.
        This is a safety net — the RevenueCat EXPIRATION webhook is the primary signal,
        but webhooks can be delayed or missed. Also logs an EXPIRED event to subscriptions table.
        """
        from sqlalchemy import text as _text3
        while True:
            await asyncio.sleep(900)  # check every 15 minutes
            try:
                async with AsyncSessionLocal() as db:
                    now = datetime.now(timezone.utc)
                    # Find all paid users whose subscription has lapsed
                    result = await db.execute(
                        _text3("""
                            SELECT id, subscription_tier, subscription_expires_at, revenuecat_customer_id
                            FROM users
                            WHERE subscription_tier IN ('pro', 'premium_plus')
                              AND subscription_expires_at IS NOT NULL
                              AND subscription_expires_at < :now
                        """).bindparams(now=now)
                    )
                    expired_users = result.fetchall()
                    if expired_users:
                        ids = [str(row[0]) for row in expired_users]
                        # Log EXPIRED events
                        for row in expired_users:
                            await db.execute(
                                _text3("""
                                    INSERT INTO subscriptions
                                      (user_id, event_type, tier, revenuecat_customer_id, expires_at)
                                    VALUES
                                      (CAST(:uid AS uuid), 'AUTO_EXPIRED', 'free', :rc_id, :expires_at)
                                """).bindparams(
                                    uid=str(row[0]),
                                    rc_id=row[3],
                                    expires_at=row[2],
                                )
                            )
                        # Bulk downgrade
                        await db.execute(
                            _text3("""
                                UPDATE users
                                SET subscription_tier = 'free', subscription_expires_at = NULL
                                WHERE id = ANY(CAST(:ids AS uuid[]))
                            """).bindparams(ids=ids)
                        )
                        await db.commit()
                        _log.info(
                            "Auto-expired %d subscription(s): %s",
                            len(expired_users), ids,
                        )
            except Exception as exc:
                _log.warning("Subscription expiry loop error (non-critical): %s", exc)

    async def _marketing_scheduler():
        """
        Every 30 minutes: send marketing push notifications to users in countries
        whose current local hour matches one of the configured peak_hours.

        Language is auto-detected per user from their spoken languages list.
        Scheduler deduplication: skips if a send for the same (code, tz_name, hour)
        already ran within the last 55 minutes.
        """
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        from sqlalchemy import select as _sel
        from app.db.session import AsyncSessionLocal
        from app.models.marketing import MarketingCountry, MarketingTemplate, MarketingCampaign
        from app.models.user import User
        from app.api.v1.endpoints.marketing import _execute_send, _lang_lookup_cache
        from datetime import datetime, timezone, timedelta

        while True:
            await asyncio.sleep(1800)  # run every 30 minutes
            try:
                now_utc = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as db:
                    # Load all active countries
                    result = await db.execute(
                        _sel(MarketingCountry).where(MarketingCountry.is_active.is_(True))
                    )
                    countries = result.scalars().all()

                    for country in countries:
                        try:
                            tz = ZoneInfo(country.tz_name)
                        except ZoneInfoNotFoundError:
                            _log.warning("marketing_scheduler | unknown tz: %s", country.tz_name)
                            continue

                        local_now = now_utc.astimezone(tz)
                        local_hour = local_now.hour

                        if local_hour not in (country.peak_hours or []):
                            continue

                        # Dedup: skip if already sent for this country+tz+hour in last 55 min
                        cutoff = now_utc - timedelta(minutes=55)
                        existing = await db.execute(
                            _sel(MarketingCampaign).where(
                                MarketingCampaign.triggered_by == "scheduler",
                                MarketingCampaign.target_value == country.code,
                                MarketingCampaign.scheduler_tz == country.tz_name,
                                MarketingCampaign.scheduler_hour == local_hour,
                                MarketingCampaign.created_at >= cutoff,
                            ).limit(1)
                        )
                        if existing.scalar_one_or_none() is not None:
                            continue  # already sent this hour for this country/tz

                        # Use PostgreSQL advisory lock to prevent race conditions
                        # Lock key = hash(country_code + tz + hour) to ensure only 1 scheduler sends per hour
                        lock_key = hash(f"{country.code}:{country.tz_name}:{local_hour}") % (2**31)
                        from sqlalchemy import text as _text
                        lock_acquired = await db.execute(_text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key})
                        if not lock_acquired.scalar():
                            continue  # another scheduler instance is handling this country/hour
                        
                        # Double-check after acquiring lock (another instance might have sent while we waited)
                        existing = await db.execute(
                            _sel(MarketingCampaign).where(
                                MarketingCampaign.triggered_by == "scheduler",
                                MarketingCampaign.target_value == country.code,
                                MarketingCampaign.scheduler_tz == country.tz_name,
                                MarketingCampaign.scheduler_hour == local_hour,
                                MarketingCampaign.created_at >= cutoff,
                            ).limit(1)
                        )
                        if existing.scalar_one_or_none() is not None:
                            continue

                        # Send — language auto-detected per user, country is the targeting key
                        try:
                            res = await _execute_send(
                                db,
                                target="country",
                                target_value=country.code,
                                template_id=None,      # will auto-pick template per user language
                                custom_title=None,
                                custom_body=None,
                                language_override=None,
                                triggered_by="scheduler",
                                campaign_name=f"Scheduler · {country.name} · {local_hour:02d}:00",
                                scheduler_tz=country.tz_name,
                                scheduler_hour=local_hour,
                            )
                            _log.info(
                                "marketing_scheduler | %s (%s) %02d:00 → sent=%d failed=%d",
                                country.name, country.tz_name, local_hour,
                                res.get("sent", 0), res.get("failed", 0),
                            )
                        except Exception as send_exc:
                            _log.warning(
                                "marketing_scheduler | send error for %s (%s): %s",
                                country.name, country.tz_name, send_exc,
                            )

            except Exception as outer_exc:
                _log.warning("marketing_scheduler | loop error (non-critical): %s", outer_exc)

    async def _enable_bypass_location_for_tester():
        """
        On every startup: ensure the owner account has full admin access and
        location-filter bypass so they can use all admin endpoints and see
        profiles from anywhere — no separate admin account needed.
        """
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models.user import User

        _OWNER_PHONE = "9148880196"
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(User).where(User.phone == _OWNER_PHONE)
                )
                user = result.scalar_one_or_none()
                if user:
                    changed = False
                    if not user.is_admin:
                        user.is_admin = True
                        changed = True
                    if not user.bypass_location_filter:
                        user.bypass_location_filter = True
                        changed = True
                    # Do NOT clear filter preferences — the owner should be able
                    # to set and test filters just like any regular user.
                    # bypass_location_filter=True already lets them see everyone
                    # when no filter is applied.
                    if changed:
                        await db.commit()
                        _log.info(
                            "Owner account %s (phone %s): is_admin=True, bypass_location_filter=True, all filters cleared",
                            user.id, _OWNER_PHONE,
                        )
        except Exception as exc:
            _log.warning("Could not configure owner account privileges: %s", exc)

    _bg_tasks = {
        asyncio.create_task(_recover_stale_attempts()),
        asyncio.create_task(_expire_travel_modes()),
        asyncio.create_task(_enable_bypass_location_for_tester()),
        asyncio.create_task(_expire_subscriptions()),
        asyncio.create_task(_marketing_scheduler()),
        # Migrations run fully in background — zero startup latency
        asyncio.create_task(_run_migrations_bg()),
    }
    # Keep strong references so GC doesn't discard them before they finish
    for _t in _bg_tasks:
        _t.add_done_callback(_bg_tasks.discard)
    
    yield
    # Dispose engine on shutdown
    await engine.dispose()


import logging as _logging
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse as _JSONResponse

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.limiter import limiter

_main_log = _logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Debug middleware to log PATCH requests to /profile/me
@app.middleware("http")
async def log_profile_requests(request: Request, call_next):
    if request.method == "PATCH" and "/profile/me" in str(request.url):
        body = await request.body()
        print(f"\n🔍 DEBUG: PATCH /profile/me")
        print(f"   Raw body: {body[:500]}")  # First 500 bytes
        try:
            import json
            parsed = json.loads(body) if body else {}
            print(f"   Parsed JSON: {parsed}")
        except:
            print(f"   Could not parse as JSON")
        # Important: We need to reconstruct the request with the body we just read
        # because request.body() can only be called once
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive
    response = await call_next(request)
    return response

# Log Pydantic request validation errors so we can debug 422s
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    _main_log.warning(
        "422 RequestValidationError on %s %s — errors: %s",
        request.method, request.url.path, errors,
    )
    # Also print to console for debugging
    print(f"\n⚠️  422 VALIDATION ERROR on {request.method} {request.url.path}")
    print(f"Errors: {errors}")
    return _JSONResponse(status_code=422, content={"detail": errors})


# Return a clean 503 for any DB connection errors that slip past the retry in
# get_db() rather than letting them surface as raw 500 tracebacks.
@app.exception_handler(DBAPIError)
async def db_connection_error_handler(request: Request, exc: DBAPIError):
    orig = getattr(exc, "orig", None)
    if isinstance(orig, (ConnectionDoesNotExistError, TooManyConnectionsError)):
        _main_log.warning(
            "503 DB connection error on %s %s: %s",
            request.method, request.url.path, type(orig).__name__,
        )
        return _JSONResponse(
            status_code=503,
            content={"detail": "Database temporarily unavailable. Please retry."},
        )
    raise exc

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://zod.ailoo.co",
        "https://dev.zod.ailoo.co",
        "http://localhost:8081",
        "http://localhost:19006",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── X-App-Key gate ────────────────────────────────────────────────────────────
# Requests from the mobile app must carry:  X-App-Key: <APP_API_KEY>
# Docs, health, and openapi schema are always public.

_APP_KEY_PUBLIC = (
    "/health", "/", "/openapi.json", "/docs", "/redoc",
    # LinkedIn OAuth callback — LinkedIn redirects here directly (no app key)
    "/api/v1/linkedin/callback",
    # WebSocket connections use the WS constructor, not fetch — can't send custom headers
    "/api/v1/ws",
    "/ws",
    # RevenueCat webhook — authenticated by its own Authorization header, not app key
    "/api/v1/subscription/webhook",
)

# ── Scan-required in-memory cache ─────────────────────────────────────────────
# Avoids a DB hit on every authenticated request.
# Entry: user_id → (face_req, id_req, expires_at_monotonic)
# TTL: 60 seconds — short enough to pick up new flags quickly.
_SCAN_CACHE_TTL = 60.0
_scan_cache: Dict[str, Tuple[bool, bool, float]] = {}


@app.middleware("http")
async def app_key_gate(request: Request, call_next):
    _key = settings.APP_API_KEY
    if not _key:
        return await call_next(request)

    path = request.url.path
    if any(path == p or path.startswith(p + "/") for p in _APP_KEY_PUBLIC):
        return await call_next(request)

    provided = request.headers.get("X-App-Key", "")
    if provided != _key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing app key."},
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return await call_next(request)

# ── Security headers ──────────────────────────────────────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if not settings.DEBUG:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response

# ── Scan-required API gate ────────────────────────────────────────────────────
# When a user has face_scan_required=True or id_scan_required=True, every API
# call is blocked with HTTP 423 EXCEPT the allowlisted paths below.
# This enforces compliance server-side — the frontend cannot bypass it.

_SCAN_GATE_ALLOW = (
    "/api/v1/auth/",          # login, refresh, OTP
    "/api/v1/upload/verify-face",  # face scan submit + status + history
    "/api/v1/upload/verify-id",    # ID scan submit + status
    "/api/v1/profile/me",     # read/update profile (needed to clear the flag)
    "/api/v1/ws/",            # WebSockets (face-scan-required push)
    "/ws/",
    "/docs", "/redoc", "/openapi.json", "/health", "/",
)


@app.middleware("http")
async def scan_required_gate(request: Request, call_next):
    import asyncio as _asyncio
    path = request.url.path

    # Fast-path: always allow exempt routes without touching the DB
    if any(path.startswith(p) for p in _SCAN_GATE_ALLOW):
        try:
            return await call_next(request)
        except _asyncio.CancelledError:
            raise  # server shutdown — propagate cleanly without logging as ERROR

    # Extract Bearer token (no-op if missing — other middleware handles 401)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        try:
            return await call_next(request)
        except _asyncio.CancelledError:
            raise

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return await call_next(request)
        uid = _uuid.UUID(user_id)
    except (JWTError, ValueError):
        return await call_next(request)

    # Check in-memory cache first to avoid a DB hit on every authenticated request
    from sqlalchemy import text as _text2
    uid_str = str(uid)
    now_mono = time.monotonic()

    cached = _scan_cache.get(uid_str)
    if cached and now_mono < cached[2]:
        face_req, id_req = cached[0], cached[1]
        _cached_is_onboarded = cached[3] if len(cached) > 3 else True
    else:
        try:
            async with AsyncSessionLocal() as db:
                row = (await db.execute(
                    _text2("SELECT face_scan_required, id_scan_required, is_onboarded FROM users WHERE id = :uid"),
                    {"uid": uid_str},
                )).fetchone()
        except _asyncio.CancelledError:
            raise  # shutdown — propagate without noise
        except Exception:
            return await call_next(request)

        if row is None:
            return await call_next(request)

        face_req, id_req = bool(row[0]), bool(row[1])
        # Store is_onboarded (index 2) in cache to avoid a second DB hit in the 423 block
        _scan_cache[uid_str] = (face_req, id_req, now_mono + _SCAN_CACHE_TTL, bool(row[2]))
        _cached_is_onboarded = bool(row[2])

    if face_req:
        # Determine whether this is a normal onboarding step or a compliance re-check.
        # FE uses `flow` to decide which screen to show:
        #   "onboarding"  → friendly camera UI: "Let's verify it's you"
        #   "compliance"  → re-verification prompt
        _flow = "compliance" if _cached_is_onboarded else "onboarding"

        return JSONResponse(
            status_code=423,
            content={
                "detail": (
                    "Almost there! Please verify your face to complete setup."
                    if _flow == "onboarding"
                    else "Please complete face verification to continue."
                ),
                "code": "face_scan_required",
                "flow": _flow,   # "onboarding" | "compliance"
            },
        )
    if id_req:
        return JSONResponse(
            status_code=423,
            content={
                "detail": "ID verification required before accessing this feature.",
                "code": "id_scan_required",
                "flow": "compliance",
            },
        )

    try:
        return await call_next(request)
    except _asyncio.CancelledError:
        raise  # shutdown — propagate cleanly


app.include_router(api_router)


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
