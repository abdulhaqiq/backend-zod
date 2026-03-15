from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine

# Import all models so Base.metadata knows about every table
import app.models.user  # noqa: F401
import app.models.otp  # noqa: F401
import app.models.refresh_token  # noqa: F401
import app.models.pickup_line  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(lambda conn: Base.metadata.create_all(conn, checkfirst=True))
    
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

app.include_router(api_router)


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
