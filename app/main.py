from contextlib import asynccontextmanager
import uuid as _uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError

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
import app.models.gift_card  # noqa: F401
import app.models.user_report  # noqa: F401
import app.models.message  # noqa: F401
import app.models.message_reaction  # noqa: F401
import app.models.tod_round  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup (no-op for existing tables)
    async with engine.begin() as conn:
        await conn.run_sync(lambda conn: Base.metadata.create_all(conn, checkfirst=True))

    # Incremental column migrations (safe to run on every restart)
    from sqlalchemy import text as _text
    async with engine.begin() as conn:
        await conn.execute(_text(
            "ALTER TABLE user_scores ADD COLUMN IF NOT EXISTS profile_hash VARCHAR(32)"
        ))
        await conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS id_scan_required BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        await conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS travel_expires_at TIMESTAMPTZ"
        ))
        await conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_latitude DOUBLE PRECISION"
        ))
        await conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_longitude DOUBLE PRECISION"
        ))
        await conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_city VARCHAR(128)"
        ))
        await conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS real_country VARCHAR(128)"
        ))
        # Message enhancements
        await conn.execute(_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ"
        ))
        await conn.execute(_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ"
        ))
        # user_compatibility is created by create_all above; nothing to backfill
    
    import asyncio
    import logging
    _log = logging.getLogger(__name__)

    # Pre-download DeepFace models to avoid blocking first verification
    async def _warmup_deepface():
        """
        Pre-download production models:
          - RetinaFace detector (fast, accurate)
          - ArcFace recognition model (SOTA for face matching)
        This prevents blocking during first verification.
        """
        try:
            _log.info("DeepFace warmup: pre-loading production models...")
            from deepface import DeepFace
            import numpy as np

            dummy = np.ones((224, 224, 3), dtype=np.uint8) * 128

            try:
                DeepFace.represent(
                    img_path=dummy,
                    model_name="ArcFace",
                    detector_backend="skip",
                    enforce_detection=False,
                )
                _log.info("✓ ArcFace model loaded")
            except Exception as e:
                _log.debug("ArcFace warmup (expected on dummy): %s", e)

            try:
                DeepFace.extract_faces(
                    img_path=dummy,
                    detector_backend="retinaface",
                    enforce_detection=False,
                )
                _log.info("✓ RetinaFace detector loaded")
            except Exception as e:
                _log.debug("RetinaFace warmup (expected on dummy): %s", e)

            _log.info("DeepFace production models ready")
        except Exception as exc:
            _log.warning("DeepFace warmup failed (non-critical): %s", exc)

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

    asyncio.create_task(_warmup_deepface())
    asyncio.create_task(_recover_stale_attempts())
    asyncio.create_task(_expire_travel_modes())
    
    yield
    # Dispose engine on shutdown
    await engine.dispose()


from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.limiter import limiter

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
)


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
    path = request.url.path

    # Fast-path: always allow exempt routes without touching the DB
    if any(path.startswith(p) for p in _SCAN_GATE_ALLOW):
        return await call_next(request)

    # Extract Bearer token (no-op if missing — other middleware handles 401)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return await call_next(request)

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return await call_next(request)
        uid = _uuid.UUID(user_id)
    except (JWTError, ValueError):
        return await call_next(request)

    # Single lightweight query — only fetch the two flag columns
    from sqlalchemy import select, text as _text2
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            _text2("SELECT face_scan_required, id_scan_required FROM users WHERE id = :uid"),
            {"uid": str(uid)},
        )).fetchone()

    if row is None:
        return await call_next(request)

    face_req, id_req = bool(row[0]), bool(row[1])

    if face_req:
        return JSONResponse(
            status_code=423,
            content={
                "detail": "Face verification required before accessing this feature.",
                "code": "face_scan_required",
            },
        )
    if id_req:
        return JSONResponse(
            status_code=423,
            content={
                "detail": "ID verification required before accessing this feature.",
                "code": "id_scan_required",
            },
        )

    return await call_next(request)


app.include_router(api_router)


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
