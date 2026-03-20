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

    asyncio.create_task(_warmup_deepface())
    asyncio.create_task(_recover_stale_attempts())
    
    yield
    # Dispose engine on shutdown
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
