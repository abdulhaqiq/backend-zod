"""
Upload endpoints:
  POST /upload/photo        — analyze + upload a photo to DO Spaces, returns CDN URL + analysis
  POST /upload/audio        — upload a voice clip (m4a/aac/mp4) to DO Spaces, returns CDN URL
  POST /upload/transcribe   — transcribe audio via OpenAI Whisper, returns text
  POST /upload/verify-face  — submit face scan (returns pending immediately, analysed in bg)
  GET  /upload/verify-face/status  — latest attempt status for the current user
  GET  /upload/verify-face/history — all past attempts for the current user
"""
import asyncio
import hashlib
import logging
import uuid as _uuid
from dataclasses import asdict
from datetime import datetime, timezone

import io
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from app.core.deps import get_current_user
from app.core.photo_analyzer import analyze_photo
from app.core.storage import upload_file, upload_photo
from app.db.session import AsyncSessionLocal, get_db
from app.models.lookup import LookupOption
from app.models.user import User
from app.models.verification import VerificationAttempt
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
ALLOWED_AUDIO_TYPES = {
    "audio/m4a", "audio/x-m4a", "audio/aac",
    "audio/mp4", "video/mp4",           # iOS records .m4a under video/mp4 sometimes
    "audio/mpeg", "audio/ogg",
}
MAX_IMAGE_MB = 10
MAX_AUDIO_MB = 20
MAX_AUDIO_SECONDS = 35  # a little over 30 to allow for encoding overhead


FACE_MATCH_THRESHOLD = 60.0   # AWS Rekognition CompareFaces similarity threshold (selfie vs profile photos)


async def _check_duplicate_photo(contents: bytes, existing_urls: list[str]) -> str | None:
    """
    Check if the uploaded photo is a duplicate of any existing photos.
    Returns the duplicate URL if found, None otherwise.
    Uses MD5 hash for fast comparison.
    """
    if not existing_urls:
        return None
    
    # Calculate hash of uploaded photo
    upload_hash = hashlib.md5(contents).hexdigest()
    
    # Download and compare each existing photo
    async with httpx.AsyncClient(timeout=10) as client:
        for url in existing_urls:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    existing_hash = hashlib.md5(response.content).hexdigest()
                    if upload_hash == existing_hash:
                        return url
            except Exception as exc:
                _log.warning("Failed to download photo for duplicate check: %s", exc)
                continue
    
    return None


_REKOGNITION_MAX_BYTES = 5 * 1024 * 1024   # 5 MB hard limit
_JPEG_MAX_DIMENSION   = 1920               # resize long-edge before quality reduction
_STORAGE_DIMENSION    = 1024              # final square crop size stored to DO Spaces


def _crop_square_1024(img_bytes: bytes) -> bytes:
    """
    Center-crop the image to a square, then resize to 1024×1024.
    This is applied to every profile photo before it is uploaded to storage.

    Steps:
      1. Open with HEIC support
      2. Center-crop to the smaller dimension (portrait → square, landscape → square)
      3. Resize to 1024×1024 with high-quality Lanczos resampling
      4. Save as JPEG quality 88
    """
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size

    # Center-crop to square
    side = min(w, h)
    left   = (w - side) // 2
    top    = (h - side) // 2
    img    = img.crop((left, top, left + side, top + side))

    # Resize to 1024×1024
    img = img.resize((_STORAGE_DIMENSION, _STORAGE_DIMENSION), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _to_jpeg_bytes(img_bytes: bytes) -> bytes:
    """
    Convert any format (JPEG/PNG/WebP/HEIC) to a JPEG under Rekognition's 5 MB limit.
    Resizes long edge to 1920 px, then reduces quality until size is within limits.
    """
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

    w, h = img.size
    if max(w, h) > _JPEG_MAX_DIMENSION:
        scale = _JPEG_MAX_DIMENSION / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    for quality in (85, 75, 65, 50, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= _REKOGNITION_MAX_BYTES:
            return buf.getvalue()

    w2, h2 = img.size
    img = img.resize((w2 // 2, h2 // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=40, optimize=True)
    return buf.getvalue()


def _rekognition_client():
    """Return a boto3 Rekognition client using AWS credentials from settings."""
    import boto3
    from botocore.config import Config
    from app.core.config import settings
    return boto3.client(
        "rekognition",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=settings.AWS_REGION or "us-east-1",
        config=Config(
            connect_timeout=8,   # seconds to establish connection
            read_timeout=15,     # seconds to wait for a response
            retries={"max_attempts": 1},  # no retries — fail fast, caller handles
        ),
    )


def _detect_face_fast(img_bytes: bytes):
    """
    Face detection via AWS Rekognition DetectFaces.
    Returns (success, reason, age, jpeg_bytes, face_bounding_box).
    Quality (sharpness + brightness) is read from Rekognition's Quality field — no NumPy/Pillow math.
    """
    try:
        jpeg_bytes = _to_jpeg_bytes(img_bytes)
    except Exception as exc:
        _log.warning("face-detect | image conversion failed: %s", exc)
        return False, "Could not read image. Please try a different photo.", None, None, None

    try:
        client = _rekognition_client()
        resp   = client.detect_faces(Image={"Bytes": jpeg_bytes}, Attributes=["ALL"])
        faces  = resp.get("FaceDetails", [])

        if not faces:
            return False, "No face detected. Make sure your face is clearly visible and well-lit.", None, None, None

        face       = faces[0]
        confidence = face.get("Confidence", 0.0)
        if confidence < 70.0:
            return False, "Face not clearly visible. Please ensure good lighting and look directly at the camera.", None, None, None

        quality    = face.get("Quality", {})
        brightness = float(quality.get("Brightness", 50.0))
        sharpness  = float(quality.get("Sharpness",  50.0))

        if brightness < 10.0:
            return False, "Image too dark. Please find better lighting.", None, None, None
        if brightness > 95.0:
            return False, "Image overexposed. Avoid direct bright light.", None, None, None
        if sharpness < 20.0:
            return False, "Image is too blurry. Please upload a sharper photo.", None, None, None

        age_range = face.get("AgeRange", {})
        age = (age_range.get("Low", 0) + age_range.get("High", 0)) // 2

        _log.info("face-detect [Rekognition] | confidence=%.1f%% brightness=%.0f sharpness=%.0f age=%s",
                  confidence, brightness, sharpness, age)
        return True, None, age, jpeg_bytes, face.get("BoundingBox", {})

    except Exception as exc:
        _log.warning("face-detect | Rekognition failed: %s", exc)
        return False, "Could not process image. Please try again.", None, None, None


def _compare_selfie_to_url(selfie_jpeg: bytes, url: str) -> float:
    """Compare selfie against a single profile photo URL. Returns similarity %."""
    try:
        resp = httpx.get(url, timeout=8, follow_redirects=True)
        if resp.status_code != 200:
            _log.warning("[FACE MATCH] Could not fetch %s (HTTP %d)", url.split("/")[-1][:24], resp.status_code)
            return 0.0

        target_jpeg = _to_jpeg_bytes(resp.content)
        client = _rekognition_client()
        rek_resp = client.compare_faces(
            SourceImage={"Bytes": selfie_jpeg},
            TargetImage={"Bytes": target_jpeg},
            SimilarityThreshold=0,
        )
        matches = rek_resp.get("FaceMatches", [])
        pct = float(matches[0]["Similarity"]) if matches else 0.0
        _log.info("[FACE MATCH] Rekognition vs %s → %.1f%%", url.split("/")[-1][:24], pct)
        return pct
    except Exception as exc:
        _log.warning("[FACE MATCH] Error vs %s: %s", url.split("/")[-1][:24], exc)
        return 0.0


def _match_against_photos(
    selfie_img_bytes: bytes,
    photo_urls: list[str],
) -> tuple[float, int]:
    """
    Compare selfie against up to 3 profile photos in parallel using Rekognition.
    Returns (best_similarity_pct, photos_compared).
    Passing requires best_pct >= FACE_MATCH_THRESHOLD (any one photo is enough).
    """
    if not photo_urls:
        return 0.0, 0

    try:
        selfie_jpeg = _to_jpeg_bytes(selfie_img_bytes)
    except Exception as exc:
        _log.warning("Could not convert selfie to JPEG: %s — using raw bytes as fallback", exc)
        selfie_jpeg = selfie_img_bytes  # already JPEG when passed from _detect_face_fast

    urls = photo_urls[:3]  # always cap at 3

    from concurrent.futures import ThreadPoolExecutor, as_completed
    scores: list[float] = []

    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = {pool.submit(_compare_selfie_to_url, selfie_jpeg, u): u for u in urls}
        for fut in as_completed(futures):
            scores.append(fut.result())

    compared  = len(scores)
    best_pct  = round(max(scores), 1) if scores else 0.0
    _log.info(
        "[FACE MATCH] %d photos compared | scores=%s | best=%.1f%%",
        compared, [round(s, 1) for s in scores], best_pct,
    )
    return best_pct, compared


# ─── Background verification task ─────────────────────────────────────────────

async def _process_verification(
    attempt_id: _uuid.UUID,
    selfie_bytes: bytes,
    photo_urls: list[str],
) -> None:
    """
    Runs after the HTTP response is returned.
    Performs liveness + face-match, then updates the attempt record and user row.
    Hard timeout: 120 s (2 minutes) total.
    """
    _log.info("bg-verify | attempt=%s STARTED", attempt_id)
    try:
        await asyncio.wait_for(
            _run_face_verification(attempt_id, selfie_bytes, photo_urls),
            timeout=120,
        )
    except asyncio.TimeoutError:
        _log.error("bg-verify | attempt=%s TIMED OUT after 120s (2 min)", attempt_id)
        try:
            async with AsyncSessionLocal() as db:
                attempt = await db.get(VerificationAttempt, attempt_id)
                if attempt and attempt.status == "pending":
                    attempt.status = "rejected"
                    attempt.rejection_reason = "Verification took too long. Please try again with better lighting."
                    attempt.processed_at = datetime.now(timezone.utc)
                    user = await db.get(User, attempt.user_id)
                    if user:
                        user.verification_status = "rejected"
                    await db.commit()
                    
                    # Notify WebSocket clients to show "try again"
                    try:
                        from app.api.v1.endpoints.verification_ws import watcher as _watcher
                        await _watcher.notify(user.id, {
                            "status": "rejected",
                            "rejection_reason": "Verification took too long. Please try again with better lighting.",
                            "face_match_score": None,
                            "navigate_to": "retry",
                            "flow": "onboarding",
                        })
                    except Exception:
                        pass
                    
                    # Send push notification
                    try:
                        from app.core.push import notify_user
                        await notify_user(
                            user, "verification",
                            title="Face Verification Timeout",
                            body="Verification took too long. Please try again with better lighting.",
                            data={
                                "type": "verification_rejected",
                                "rejection_reason": "Verification took too long",
                                "navigate_to": "retry",
                            },
                        )
                    except Exception:
                        pass
        except Exception as e:
            _log.error("bg-verify | timeout cleanup failed: %s", e)


async def _run_face_verification(
    attempt_id: _uuid.UUID,
    selfie_bytes: bytes,
    photo_urls: list[str],
) -> None:
    # Step 1: Fetch attempt and user data (quick DB query)
    user_id: _uuid.UUID | None = None
    async with AsyncSessionLocal() as db:
        attempt: VerificationAttempt | None = await db.get(VerificationAttempt, attempt_id)
        if not attempt:
            _log.warning("bg-verify | attempt=%s not found in DB", attempt_id)
            return
        user: User | None = await db.get(User, attempt.user_id)
        if not user:
            _log.warning("bg-verify | user not found for attempt=%s", attempt_id)
            return
        user_id = user.id
    
    # Step 2: Perform all long-running Rekognition operations WITHOUT holding DB connection
    try:
        _log.info("bg-verify | attempt=%s running face detection...", attempt_id)
        # Layer 1 — face detection + quality check
        success, reason, age_estimate, selfie_array, face_region = await asyncio.to_thread(
            _detect_face_fast, selfie_bytes
        )
        
        if not success:
            # Update DB with rejection
            async with AsyncSessionLocal() as db:
                attempt = await db.get(VerificationAttempt, attempt_id)
                user = await db.get(User, user_id)
                if attempt and user:
                    attempt.is_live = success
                    attempt.age_estimate = age_estimate
                    attempt.status = "rejected"
                    attempt.rejection_reason = reason
                    attempt.processed_at = datetime.now(timezone.utc)
                    user.verification_status = "rejected"
                    await db.commit()
                    
                    # Send push notification
                    try:
                        from app.core.push import notify_user
                        await notify_user(
                            user, "verification",
                            title="Face Verification Failed",
                            body=reason or "Please try again with better lighting.",
                            data={
                                "type": "verification_rejected",
                                "rejection_reason": reason,
                                "navigate_to": "retry",
                            },
                        )
                    except Exception as push_exc:
                        _log.warning("bg-verify | push notification failed: %s", push_exc)
                    
                    # Notify WebSocket
                    try:
                        from app.api.v1.endpoints.verification_ws import watcher as _watcher
                        await _watcher.notify(user_id, {
                            "status": "rejected",
                            "rejection_reason": reason,
                            "face_match_score": None,
                            "navigate_to": "retry",
                            "flow": "onboarding",
                        })
                    except Exception:
                        pass
            _log.info("bg-verify | attempt=%s FACE DETECTION FAIL: %s", attempt_id, reason)
            return

        # Layer 2 — face match
        if not photo_urls:
            async with AsyncSessionLocal() as db:
                attempt = await db.get(VerificationAttempt, attempt_id)
                user = await db.get(User, user_id)
                if attempt and user:
                    attempt.is_live = success
                    attempt.age_estimate = age_estimate
                    attempt.status = "rejected"
                    attempt.rejection_reason = "No profile photos found. Please upload photos first."
                    attempt.processed_at = datetime.now(timezone.utc)
                    user.verification_status = "rejected"
                    await db.commit()
                    
                    # Send push notification
                    try:
                        from app.core.push import notify_user
                        await notify_user(
                            user, "verification",
                            title="Face Verification Failed",
                            body="No profile photos found. Please upload photos first.",
                            data={
                                "type": "verification_rejected",
                                "rejection_reason": "No profile photos found",
                                "navigate_to": "photos",
                            },
                        )
                    except Exception as push_exc:
                        _log.warning("bg-verify | push notification failed: %s", push_exc)
                    
                    # Notify WebSocket
                    try:
                        from app.api.v1.endpoints.verification_ws import watcher as _watcher
                        await _watcher.notify(user_id, {
                            "status": "rejected",
                            "rejection_reason": "No profile photos found. Please upload photos first.",
                            "face_match_score": None,
                            "navigate_to": "photos",
                            "flow": "onboarding",
                        })
                    except Exception:
                        pass
            _log.info("bg-verify | attempt=%s NO PHOTOS", attempt_id)
            return

        _log.info("bg-verify | attempt=%s running face match vs %d photos...", attempt_id, len(photo_urls))
        selfie_jpeg_for_match = selfie_array if selfie_array else selfie_bytes
        best_pct, compared = await asyncio.to_thread(
            _match_against_photos, selfie_jpeg_for_match, photo_urls
        )
        
        passed = best_pct >= FACE_MATCH_THRESHOLD
        _log.info("bg-verify | attempt=%s face match result: %.1f%% (compared=%d, passed=%s)", attempt_id, best_pct, compared, passed)

        # Step 3: Open a NEW DB session to save results
        async with AsyncSessionLocal() as db:
            attempt = await db.get(VerificationAttempt, attempt_id)
            user = await db.get(User, user_id)
            if not attempt or not user:
                _log.error("bg-verify | attempt or user disappeared during processing")
                return
            
            attempt.is_live = success
            attempt.age_estimate = age_estimate
            attempt.face_match_score = best_pct
            attempt.processed_at = datetime.now(timezone.utc)

            if passed:
                attempt.status = "verified"
                user.is_verified = True
                user.verification_status = "verified"
                user.face_match_score = best_pct
                user.face_scan_required = False
                
                # Bust the in-memory 423 cache
                try:
                    from app.main import _scan_cache
                    _scan_cache.pop(str(user.id), None)
                except Exception:
                    pass
                
                # Store selfie (cropped 1024×1024)
                try:
                    from app.core.storage import upload_file as _upload_file
                    selfie_cropped = await asyncio.to_thread(_crop_square_1024, selfie_bytes)
                    selfie_cdn = await asyncio.to_thread(
                        _upload_file,
                        selfie_cropped, "image/jpeg",
                        folder=f"users/{user.id}/selfies",
                        ext=".jpg",
                    )
                    attempt.selfie_url = selfie_cdn
                except Exception as exc:
                    _log.warning("bg-verify | selfie upload failed (non-critical): %s", exc)
            else:
                attempt.status = "rejected"
                attempt.rejection_reason = (
                    "Your selfie doesn't match the photos on your profile. "
                    "Please take a clear selfie showing your face."
                    if compared > 0
                    else "Could not compare faces. Make sure your profile photos clearly show your face."
                )
                user.verification_status = "rejected"

            await db.commit()

        _log.info(
            "bg-verify | attempt=%s passed=%s match=%.1f%% compared=%d",
            attempt_id, passed, best_pct, compared,
        )

        # Push result to WebSocket clients
        from app.api.v1.endpoints.verification_ws import watcher as _watcher
        await _watcher.notify(user_id, {
            "status":           "verified" if passed else "rejected",
            "face_match_score": best_pct,
            "rejection_reason": attempt.rejection_reason if not passed else None,
            "is_live":          success,
            "navigate_to":      "feed" if passed else "retry",
            "flow":             "onboarding",
        })

        # Send push notification
        # ── Send WebSocket notification for real-time UI update ──────────────────
        try:
            from app.api.v1.endpoints.chat import notify_manager
            ws_payload = {
                "type": "verification_approved" if passed else "verification_rejected",
                "verification_status": "verified" if passed else "rejected",
                "is_verified": passed,
                "face_scan_required": False,
                "match_score": best_pct if passed else None,
                "rejection_reason": attempt.rejection_reason if not passed else None,
                "navigate_to": "feed" if passed else "retry",
            }
            ws_sent = await notify_manager.send_to(str(user_id), ws_payload)
            _log.info("bg-verify | WebSocket notify user=%s sent=%s", str(user_id)[:8], ws_sent)
        except Exception as ws_exc:
            _log.warning("bg-verify | WebSocket notify failed (non-critical): %s", ws_exc)

        # ── Send push notification (backup) ────────────────────────────────────
        try:
            from app.core.push import notify_user
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
                if user:
                    if passed:
                        await notify_user(
                            user, "verification",
                            title="Face Verification Approved ✓",
                            body=f"Your face scan passed with {best_pct:.0f}% match. You're verified!",
                            data={
                                "type": "verification_approved",
                                "match_score": best_pct,
                                "navigate_to": "feed",
                            },
                        )
                    else:
                        await notify_user(
                            user, "verification",
                            title="Face Verification Failed",
                            body=attempt.rejection_reason or "Please try again with better lighting.",
                            data={
                                "type": "verification_rejected",
                                "rejection_reason": attempt.rejection_reason,
                                "navigate_to": "retry",
                            },
                        )
        except Exception as push_exc:
            _log.warning("bg-verify | push notification failed (non-critical): %s", push_exc)

    except Exception as exc:
        _log.error("bg-verify | attempt=%s CRASHED: %s", attempt_id, exc, exc_info=True)
        # Update with error status in a fresh DB session
        try:
            async with AsyncSessionLocal() as db:
                attempt = await db.get(VerificationAttempt, attempt_id)
                user = await db.get(User, user_id)
                if attempt and user:
                    attempt.status = "rejected"
                    attempt.rejection_reason = "Internal analysis error. Please try again."
                    attempt.processed_at = datetime.now(timezone.utc)
                    user.verification_status = "rejected"
                    await db.commit()
        except Exception as db_exc:
            _log.error("bg-verify | failed to save error state: %s", db_exc)
        
        # Notify WS clients of the failure
        try:
            from app.api.v1.endpoints.verification_ws import watcher as _watcher
            await _watcher.notify(user_id, {
                "status":           "rejected",
                "rejection_reason": "Internal analysis error. Please try again.",
                "face_match_score": None,
                "navigate_to":      "retry",
                "flow":             "onboarding",
            })
        except Exception:
            pass
        
        # Send push notification
        try:
            from app.core.push import notify_user
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
                if user:
                    await notify_user(
                        user, "verification",
                        title="Face Verification Error",
                        body="Something went wrong. Please try again.",
                        data={
                            "type": "verification_rejected",
                            "rejection_reason": "Internal error",
                            "navigate_to": "retry",
                        },
                    )
        except Exception:
            pass


# ─── Submit endpoint (returns immediately with pending status) ─────────────────

@router.post("/verify-face", summary="Submit a face scan — returns pending immediately")
async def verify_face_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    device_model: str | None = Form(None),
    platform: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Accepts a selfie, creates a VerificationAttempt with status='pending', and
    runs the two-layer analysis (liveness + face-match) in a background task.
    The client should poll GET /upload/verify-face/status for the result.
    """
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported type: {file.content_type}.",
        )
    contents = await file.read()
    if len(contents) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max {MAX_IMAGE_MB} MB.",
        )

    # ── Resolve client IP (proxy-aware) ───────────────────────────────────────
    forwarded_for = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else None)
    )

    # ── Rate-limit / cooldown check ──────────────────────────────────────────
    # Schedule (face attempts only):
    #   attempts 1–5  : free  (no cooldown)
    #   attempts 6–8  : 15-minute cooldown between each
    #   attempts 9–11 : 30-minute cooldown between each
    #   attempts 12–14: 2-hour  cooldown between each
    #   attempt  15+  : 24-hour cooldown (hard limit)
    from sqlalchemy import select as _sel, func as _func
    from datetime import timedelta as _td

    _face_attempts = (await db.execute(
        _sel(VerificationAttempt)
        .where(
            VerificationAttempt.user_id == current_user.id,
            VerificationAttempt.attempt_type == "face",
        )
        .order_by(VerificationAttempt.submitted_at.desc())
    )).scalars().all()

    _total = len(_face_attempts)
    _now   = datetime.now(timezone.utc)

    # Determine required cooldown based on total previous attempts
    if _total < 5:
        _required_wait = None          # first 5 — no wait
    elif _total < 8:
        _required_wait = _td(minutes=15)
    elif _total < 11:
        _required_wait = _td(minutes=30)
    elif _total < 14:
        _required_wait = _td(hours=2)
    else:
        _required_wait = _td(hours=24)

    if _required_wait is not None and _face_attempts:
        _last_at = _face_attempts[0].submitted_at
        if _last_at.tzinfo is None:
            _last_at = _last_at.replace(tzinfo=timezone.utc)
        _elapsed  = _now - _last_at
        _remaining = _required_wait - _elapsed
        if _remaining.total_seconds() > 0:
            _mins = int(_remaining.total_seconds() // 60) + 1
            if _mins >= 120:
                _wait_str = f"{_mins // 60} hour{'s' if _mins // 60 > 1 else ''}"
            else:
                _wait_str = f"{_mins} minute{'s' if _mins > 1 else ''}"
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many attempts. Please try again in {_wait_str}.",
                headers={"Retry-After": str(int(_remaining.total_seconds()))},
            )

    # ── Snapshot first 3 non-empty profile photos for face matching ──────────
    # Using the first 3 photos ensures the selfie is checked against the most
    # prominent photos on the profile (all set during onboarding).
    all_photos: list[str] = [p for p in (current_user.photos or []) if p]
    photo_urls: list[str] = all_photos[:3]
    _log.info(
        "verify-face | user=%s — matching against %d profile photos: %s",
        current_user.id, len(photo_urls),
        [u.split("/")[-1][:24] for u in photo_urls],
    )

    # ── Create attempt record ─────────────────────────────────────────────────
    attempt = VerificationAttempt(
        user_id=current_user.id,
        attempt_type="face",
        status="pending",
        ip_address=ip_address,
        device_model=device_model,
        platform=platform,
    )
    db.add(attempt)
    current_user.verification_status = "pending"
    await db.commit()
    await db.refresh(attempt)

    attempt_id = attempt.id

    # ── Queue background analysis ─────────────────────────────────────────────
    background_tasks.add_task(_process_verification, attempt_id, contents, photo_urls)

    _log.info(
        "verify-face | user=%s attempt=%s ip=%s platform=%s — queued",
        current_user.id, attempt_id, ip_address, platform,
    )

    return {
        "status":     "pending",
        "attempt_id": str(attempt_id),
    }


# ─── Status endpoint ──────────────────────────────────────────────────────────

@router.get("/verify-face/status", summary="Get latest verification attempt status")
async def get_verification_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VerificationAttempt)
        .where(VerificationAttempt.user_id == current_user.id)
        .where(VerificationAttempt.attempt_type == "face")
        .order_by(VerificationAttempt.submitted_at.desc())
        .limit(1)
    )
    attempt = result.scalar_one_or_none()

    if not attempt:
        return {"verification_status": "unverified", "attempt": None}

    return {
        "verification_status": current_user.verification_status,
        "attempt": {
            "id":               str(attempt.id),
            "status":           attempt.status,
            "submitted_at":     attempt.submitted_at.isoformat(),
            "processed_at":     attempt.processed_at.isoformat() if attempt.processed_at else None,
            "face_match_score": attempt.face_match_score,
            "rejection_reason": attempt.rejection_reason,
            "is_live":          attempt.is_live,
        },
    }


# ─── History endpoint ─────────────────────────────────────────────────────────

@router.get("/verify-face/history", summary="All verification attempts for the current user")
async def get_verification_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VerificationAttempt)
        .where(VerificationAttempt.user_id == current_user.id)
        .order_by(VerificationAttempt.submitted_at.desc())
    )
    attempts = result.scalars().all()

    return [
        {
            "id":               str(a.id),
            "status":           a.status,
            "submitted_at":     a.submitted_at.isoformat(),
            "processed_at":     a.processed_at.isoformat() if a.processed_at else None,
            "ip_address":       a.ip_address,
            "device_model":     a.device_model,
            "platform":         a.platform,
            "selfie_url":       a.selfie_url,
            "is_live":          a.is_live,
            "face_match_score": a.face_match_score,
            "age_estimate":     a.age_estimate,
            "rejection_reason": a.rejection_reason,
        }
        for a in attempts
    ]


# ─── ID Verification ──────────────────────────────────────────────────────────

def _extract_id_text(img_bytes: bytes) -> str:
    """Extract text from an ID image using AWS Rekognition DetectText."""
    try:
        jpeg_bytes = _to_jpeg_bytes(img_bytes)
        client = _rekognition_client()
        resp = client.detect_text(Image={"Bytes": jpeg_bytes})
        words = [
            det["DetectedText"]
            for det in resp.get("TextDetections", [])
            if det.get("Type") == "WORD" and det.get("Confidence", 0) >= 60
        ]
        text = " ".join(words)
        _log.info("id-ocr | rekognition extracted %d words", len(words))
        return text
    except Exception as exc:
        _log.warning("id-ocr | rekognition failed: %s", exc)
        return ""


_DATE_PATTERN = r"\b(\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4}|\d{4}[\s/\-\.]\d{1,2}[\s/\-\.]\d{1,2})\b"
_ID_NUMBER_PATTERN = r"\b[A-Z0-9]{6,12}\b"
_EXPIRY_WORDS = {"exp", "expiry", "expires", "expiration", "valid", "until", "thru"}
_DOB_WORDS    = {"dob", "born", "birth", "date of birth", "birthday"}


def _extract_years_from_text(text: str) -> list[int]:
    """Extract all 4-digit year-like numbers from OCR text."""
    import re
    return [int(y) for y in re.findall(r"\b(19[0-9]{2}|20[0-2][0-9])\b", text)]


def _analyse_id_text(text: str) -> dict:
    """
    Parse OCR text for ID field presence AND extract values for matching.
    Returns boolean flags + extracted_name_tokens + extracted_years.
    """
    import re

    lower = text.lower()
    dates  = re.findall(_DATE_PATTERN, text)
    idnums = re.findall(_ID_NUMBER_PATTERN, text)

    # Name: title-case "First Last" OR all-caps "FIRST LAST" (common on govt IDs)
    has_name = bool(
        re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", text) or
        re.search(r"\b[A-Z]{2,}\s+[A-Z]{2,}\b", text)
    )

    has_dob    = any(w in lower for w in _DOB_WORDS) and bool(dates)
    has_expiry = any(w in lower for w in _EXPIRY_WORDS) and bool(dates)
    has_number = bool(idnums)

    if not has_dob and len(dates) >= 1:
        has_dob = True
    if not has_expiry and len(dates) >= 2:
        has_expiry = True

    # Extract all alpha words (2+ chars) as lowercase tokens for name matching.
    # Include every word regardless of case — OCR output varies by document.
    name_tokens: set[str] = set()
    for word in re.findall(r"\b[A-Za-z]{2,}\b", text):
        name_tokens.add(word.lower())

    # Extract years in DOB plausible range (1930–2010)
    all_years = _extract_years_from_text(text)
    dob_years = [y for y in all_years if 1930 <= y <= 2010]

    return {
        "has_name":         has_name,
        "has_dob":          has_dob,
        "has_expiry":       has_expiry,
        "has_number":       has_number,
        "name_tokens":      name_tokens,    # set of lowercase words found on ID
        "dob_years":        dob_years,      # plausible birth years from OCR
    }


def _name_matches(profile_name: str | None, id_name_tokens: set[str], raw_text: str = "") -> bool:
    """
    Returns True if at least one meaningful part of the profile name is found
    on the ID — either as an exact token match or as a substring in the raw OCR.

    Example: profile "Abdul Kumshey" passes if the ID contains "Abdul" OR "Kumshey"
    anywhere (exact word or substring), case-insensitive.
    """
    if not profile_name:
        return False

    # Split profile name into significant parts (skip 1-char particles)
    profile_parts = [w.lower() for w in profile_name.split() if len(w) >= 2]
    if not profile_parts:
        return False

    # 1. Exact token match (ID word == profile word)
    if id_name_tokens and any(p in id_name_tokens for p in profile_parts):
        return True

    # 2. Substring fallback — OCR sometimes merges words or adds noise.
    #    Check if any profile part appears anywhere in the raw lowercase OCR text.
    if raw_text:
        lower_raw = raw_text.lower()
        if any(p in lower_raw for p in profile_parts):
            return True

    return False


def _dob_year_matches(profile_dob, id_dob_years: list[int]) -> bool:
    """
    Returns True if the user's birth year appears anywhere in the ID OCR text.
    """
    if not profile_dob or not id_dob_years:
        return False
    try:
        birth_year = profile_dob.year
    except Exception:
        return False
    return birth_year in id_dob_years


def _match_face_on_id(selfie_bytes: bytes, id_bytes: bytes) -> tuple[bool, float]:
    """
    Compare the user's live selfie against the face on their ID document.
    Uses AWS Rekognition CompareFaces (same engine as face scan — no extra deps).
    Returns (passed, match_pct).
    """
    from app.core.config import settings

    # ── Primary: AWS Rekognition CompareFaces ─────────────────────────────────
    if settings.AWS_ACCESS_KEY_ID:
        try:
            client = _rekognition_client()
            resp = client.compare_faces(
                SourceImage={"Bytes": selfie_bytes},
                TargetImage={"Bytes": id_bytes},
                SimilarityThreshold=0,
            )
            matches = resp.get("FaceMatches", [])
            if matches:
                similarity = matches[0]["Similarity"]   # 0–100
                _log.info("id-face-match | rekognition similarity=%.1f%%", similarity)
                return similarity >= FACE_MATCH_THRESHOLD, round(similarity, 1)
            _log.info("id-face-match | rekognition found no face match")
            return False, 0.0
        except Exception as exc:
            _log.warning("id-face-match | rekognition failed (%s)", exc)
            return False, 0.0

    return False, 0.0


async def _process_id_verification(
    attempt_id: _uuid.UUID,
    front_bytes: bytes,
    back_bytes: bytes | None,
    selfie_bytes: bytes | None,   # latest verified face selfie, may be None
) -> None:
    """
    Background task: OCR the ID + optionally match face on ID against selfie.
    Updates VerificationAttempt and User.
    Hard timeout: 50 s total — prevents hanging when Rekognition is slow.
    """
    _log.info("bg-id | attempt=%s STARTED", attempt_id)
    try:
        await asyncio.wait_for(
            _run_id_verification(attempt_id, front_bytes, back_bytes, selfie_bytes),
            timeout=50,
        )
    except asyncio.TimeoutError:
        _log.error("bg-id | attempt=%s TIMED OUT after 50s", attempt_id)
        try:
            from app.api.v1.endpoints.verification_ws import watcher as _watcher, _attempt_payload
            async with AsyncSessionLocal() as db:
                attempt = await db.get(VerificationAttempt, attempt_id)
                if attempt and attempt.status == "pending":
                    attempt.status = "rejected"
                    attempt.rejection_reason = "Verification timed out. Please try again."
                    attempt.processed_at = datetime.now(timezone.utc)
                    user = await db.get(User, attempt.user_id)
                    if user:
                        user.verification_status = "rejected"
                    await db.commit()
                    await _watcher.notify(user.id, _attempt_payload(attempt))
        except Exception as e:
            _log.error("bg-id | timeout cleanup failed: %s", e)


async def _run_id_verification(
    attempt_id: _uuid.UUID,
    front_bytes: bytes,
    back_bytes: bytes | None,
    selfie_bytes: bytes | None,
) -> None:
    from app.api.v1.endpoints.verification_ws import watcher as _watcher

    async with AsyncSessionLocal() as db:
        attempt: VerificationAttempt | None = await db.get(VerificationAttempt, attempt_id)
        if not attempt:
            _log.warning("bg-id | attempt=%s not found", attempt_id)
            return
        user: User | None = await db.get(User, attempt.user_id)
        if not user:
            _log.warning("bg-id | user not found for attempt=%s", attempt_id)
            return

        async def _commit_and_notify() -> None:
            """Commit the current DB state and push the result to any waiting WS."""
            await db.commit()
            from app.api.v1.endpoints.verification_ws import _attempt_payload
            try:
                await _watcher.notify(user.id, _attempt_payload(attempt))
            except Exception as e:
                _log.warning("bg-id | watcher notify failed: %s", e)

        try:
            # ── OCR front + back in parallel ──────────────────────────────────
            _log.info("bg-id | attempt=%s running OCR (front%s)...", attempt_id, "+back" if back_bytes else "")
            if back_bytes:
                front_text, back_text = await asyncio.gather(
                    asyncio.to_thread(_extract_id_text, front_bytes),
                    asyncio.to_thread(_extract_id_text, back_bytes),
                )
                _log.info("bg-id | front OCR: %s", front_text[:200])
                _log.info("bg-id | back OCR:  %s", back_text[:200])
                all_text = front_text + " " + back_text
            else:
                front_text = await asyncio.to_thread(_extract_id_text, front_bytes)
                _log.info("bg-id | front OCR: %s", front_text[:200])
                all_text = front_text

            fields = _analyse_id_text(all_text)
            attempt.id_text_detected = all_text[:2000]
            attempt.id_has_name   = fields["has_name"]
            attempt.id_has_dob    = fields["has_dob"]
            attempt.id_has_expiry = fields["has_expiry"]
            attempt.id_has_number = fields["has_number"]

            bool_fields = {k: v for k, v in fields.items() if isinstance(v, bool)}
            detected_count = sum(bool_fields.values())
            _log.info("bg-id | attempt=%s fields=%s dob_years=%s", attempt_id, bool_fields, fields["dob_years"])

            # Require at least 2 of 4 fields for "looks like an ID"
            if detected_count < 2:
                attempt.status = "rejected"
                attempt.rejection_reason = (
                    "The image doesn't look like a valid ID. "
                    "Make sure the ID is clear, well-lit and fully visible."
                )
                attempt.processed_at = datetime.now(timezone.utc)
                user.verification_status = "rejected"
                await _commit_and_notify()
                _log.info("bg-id | attempt=%s REJECTED (only %d fields detected)", attempt_id, detected_count)
                return

            # ── Name match ────────────────────────────────────────────────────
            name_match = _name_matches(user.full_name, fields["name_tokens"], all_text)
            attempt.id_name_match = name_match
            _log.info("bg-id | attempt=%s name_match=%s (profile=%r tokens=%r)",
                      attempt_id, name_match, user.full_name, fields["name_tokens"])

            if not name_match:
                attempt.status = "rejected"
                attempt.rejection_reason = (
                    "The name on the ID doesn't match your profile name. "
                    "Please upload an ID that matches the name on your account."
                )
                attempt.processed_at = datetime.now(timezone.utc)
                user.verification_status = "rejected"
                await _commit_and_notify()
                return

            # ── Birth year match ──────────────────────────────────────────────
            dob_match = _dob_year_matches(user.date_of_birth, fields["dob_years"])
            attempt.id_dob_match = dob_match
            _log.info("bg-id | attempt=%s dob_match=%s (profile_dob=%s years=%s)",
                      attempt_id, dob_match, user.date_of_birth, fields["dob_years"])

            if not dob_match:
                attempt.status = "rejected"
                attempt.rejection_reason = (
                    "The birth year on the ID doesn't match your profile date of birth. "
                    "Make sure your profile date of birth is correct and upload a matching ID."
                )
                attempt.processed_at = datetime.now(timezone.utc)
                user.verification_status = "rejected"
                await _commit_and_notify()
                return

            # ── Face match: selfie vs face on ID ──────────────────────────────
            if selfie_bytes:
                _log.info("bg-id | attempt=%s matching face on ID...", attempt_id)
                face_match, face_pct = await asyncio.to_thread(
                    _match_face_on_id, selfie_bytes, front_bytes
                )
                attempt.id_face_match_score = face_pct
                _log.info("bg-id | attempt=%s face match=%.1f%% passed=%s", attempt_id, face_pct, face_match)

                if not face_match:
                    attempt.status = "rejected"
                    attempt.rejection_reason = (
                        "The face on your ID doesn't match your selfie. "
                        "Please make sure you are uploading your own valid ID."
                    )
                    attempt.processed_at = datetime.now(timezone.utc)
                    user.verification_status = "rejected"
                    await _commit_and_notify()
                    return
            else:
                _log.info("bg-id | attempt=%s no selfie on file — skipping face match", attempt_id)
                attempt.id_face_match_score = None

            # ── Selfie vs profile photos ─────────────────────────────────────
            # Ensure the verified identity (selfie/ID) actually matches the
            # photos on the profile. Any photo that doesn't match is removed.
            # If NO profile photo matches the selfie, reject entirely.
            PROFILE_ID_MATCH_THRESHOLD = 60.0
            if selfie_bytes and user.photos:
                profile_urls = list(user.photos)
                _log.info("bg-id | attempt=%s checking selfie vs %d profile photos...",
                          attempt_id, len(profile_urls))
                kept: list[str] = []
                removed: list[str] = []
                for url in profile_urls:
                    try:
                        pct, cmp = await asyncio.to_thread(
                            _match_against_photos, selfie_bytes, [url]
                        )
                        if cmp == 0 or pct >= PROFILE_ID_MATCH_THRESHOLD:
                            kept.append(url)
                        else:
                            removed.append(url)
                            _log.warning(
                                "bg-id | attempt=%s removing non-matching photo (%.1f%% < %.0f%%): %s",
                                attempt_id, pct, PROFILE_ID_MATCH_THRESHOLD, url.split("/")[-1][:30],
                            )
                    except Exception as exc:
                        _log.warning("bg-id | photo match error for %s: %s",
                                     url.split("/")[-1][:24], exc)
                        kept.append(url)  # keep on error — don't wrongly remove

                if removed:
                    user.photos = kept if kept else []
                    _log.info("bg-id | attempt=%s removed %d fake/non-matching photos, kept %d",
                              attempt_id, len(removed), len(kept))

                if not kept:
                    attempt.status = "rejected"
                    attempt.rejection_reason = (
                        "None of your profile photos match your verified identity. "
                        "Please upload photos of yourself and try again."
                    )
                    attempt.processed_at = datetime.now(timezone.utc)
                    user.verification_status = "rejected"
                    await _commit_and_notify()
                    return

            # ── All checks passed ─────────────────────────────────────────────
            attempt.status = "verified"
            attempt.processed_at = datetime.now(timezone.utc)
            user.verification_status = "verified"
            user.is_verified = True
            await _commit_and_notify()
            _log.info("bg-id | attempt=%s VERIFIED (fields=%d, face=%.1f%%)",
                      attempt_id, detected_count, attempt.id_face_match_score or 0)

        except Exception as exc:
            _log.error("bg-id | attempt=%s CRASHED: %s", attempt_id, exc, exc_info=True)
            attempt.status = "rejected"
            attempt.rejection_reason = "Internal analysis error. Please try again."
            attempt.processed_at = datetime.now(timezone.utc)
            user.verification_status = "rejected"
            await _commit_and_notify()


@router.post("/verify-id", summary="Submit ID front + back for verification")
async def verify_id_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    front: UploadFile = File(...),
    back: UploadFile | None = File(None),
    device_model: str | None = Form(None),
    platform: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Accepts ID front (and optionally back), creates a VerificationAttempt with
    status='pending', then runs OCR + face-match in a background task.
    """
    # Validate front
    if front.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type: {front.content_type}.",
        )
    front_bytes = await front.read()
    if len(front_bytes) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Front image too large. Max {MAX_IMAGE_MB} MB.",
        )

    back_bytes: bytes | None = None
    if back and back.filename:
        if back.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported image type for back: {back.content_type}.",
            )
        back_bytes = await back.read()
        if len(back_bytes) > MAX_IMAGE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Back image too large. Max {MAX_IMAGE_MB} MB.",
            )

    # Resolve IP
    forwarded_for = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else (request.client.host if request.client else None)
    )

    # Fetch latest verified selfie bytes for face matching (if available)
    selfie_bytes: bytes | None = None
    try:
        face_attempt_result = await db.execute(
            select(VerificationAttempt)
            .where(VerificationAttempt.user_id == current_user.id)
            .where(VerificationAttempt.attempt_type == "face")
            .where(VerificationAttempt.status == "verified")
            .order_by(VerificationAttempt.submitted_at.desc())
            .limit(1)
        )
        face_attempt = face_attempt_result.scalar_one_or_none()
        if face_attempt and face_attempt.selfie_url:
            # Use asyncio.to_thread so the async event loop is not blocked
            resp = await asyncio.to_thread(
                httpx.get, face_attempt.selfie_url,
                timeout=4, follow_redirects=True,
            )
            if resp.status_code == 200:
                selfie_bytes = resp.content
    except Exception as exc:
        _log.warning("verify-id | could not fetch selfie: %s", exc)

    # Create attempt
    attempt = VerificationAttempt(
        user_id=current_user.id,
        attempt_type="id",
        status="pending",
        ip_address=ip_address,
        device_model=device_model,
        platform=platform,
    )
    db.add(attempt)
    current_user.verification_status = "pending"
    await db.commit()
    await db.refresh(attempt)

    attempt_id = attempt.id
    background_tasks.add_task(
        _process_id_verification,
        attempt_id, front_bytes, back_bytes, selfie_bytes,
    )

    _log.info(
        "verify-id | user=%s attempt=%s ip=%s — queued (back=%s, selfie=%s)",
        current_user.id, attempt_id, ip_address,
        bool(back_bytes), bool(selfie_bytes),
    )
    return {"status": "pending", "attempt_id": str(attempt_id)}


@router.get("/verify-id/status", summary="Get latest ID verification attempt status")
async def get_id_verification_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VerificationAttempt)
        .where(VerificationAttempt.user_id == current_user.id)
        .where(VerificationAttempt.attempt_type == "id")
        .order_by(VerificationAttempt.submitted_at.desc())
        .limit(1)
    )
    attempt = result.scalar_one_or_none()
    if not attempt:
        return {"verification_status": current_user.verification_status, "attempt": None}

    return {
        "verification_status": current_user.verification_status,
        "attempt": {
            "id":                  str(attempt.id),
            "status":              attempt.status,
            "submitted_at":        attempt.submitted_at.isoformat(),
            "processed_at":        attempt.processed_at.isoformat() if attempt.processed_at else None,
            "id_face_match_score": attempt.id_face_match_score,
            "id_has_name":         attempt.id_has_name,
            "id_has_dob":          attempt.id_has_dob,
            "id_has_expiry":       attempt.id_has_expiry,
            "id_has_number":       attempt.id_has_number,
            "id_name_match":       attempt.id_name_match,
            "id_dob_match":        attempt.id_dob_match,
            "rejection_reason":    attempt.rejection_reason,
        },
    }


@router.post("/audio")
async def upload_audio_endpoint(
    file: UploadFile = File(...),
    duration_sec: float = Form(...),
    current_user: User = Depends(get_current_user),
):
    # ── Validate type ─────────────────────────────────────────────────────────
    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio type: {ct}. Allowed: m4a, aac, mp4, mp3.",
        )

    # ── Validate duration ─────────────────────────────────────────────────────
    if duration_sec > MAX_AUDIO_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio too long ({duration_sec:.0f}s). Maximum is 30 seconds.",
        )

    contents = await file.read()

    # ── Validate size ─────────────────────────────────────────────────────────
    if len(contents) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max size is {MAX_AUDIO_MB} MB.",
        )

    # ── Upload to DO Spaces ───────────────────────────────────────────────────
    ext_map = {
        "audio/m4a": ".m4a", "audio/x-m4a": ".m4a",
        "audio/aac": ".aac", "audio/mp4": ".m4a", "video/mp4": ".m4a",
        "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
    }
    ext = ext_map.get(ct, ".m4a")

    try:
        cdn_url = await asyncio.to_thread(
            upload_file,
            contents, ct,
            folder=f"users/{current_user.id}/voice",
            ext=ext,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    _log.info("Audio uploaded for user %s → %s (%.1fs)", current_user.id, cdn_url, duration_sec)
    return {"url": cdn_url, "duration_sec": round(duration_sec, 1)}


@router.post("/photo", summary="Analyze + upload a photo to DigitalOcean Spaces")
async def upload_photo_endpoint(
    file: UploadFile = File(...),
    purpose: str = Form(default="profile"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a photo.

    `purpose` controls which checks are run:
      - "profile"  (default) — full pipeline: quality + NSFW + face detection + face consistency
      - "chat"               — NSFW check only (no face required, no duplicate check)
    """
    is_chat = purpose == "chat"

    # ── Validate file type ────────────────────────────────────────────────────
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported type: {file.content_type}. Allowed: jpeg, png, webp, heic.",
        )

    contents = await file.read()

    # ── Validate file size ────────────────────────────────────────────────────
    if len(contents) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max size is {MAX_IMAGE_MB} MB.",
        )

    if is_chat:
        # ── Chat photos: NSFW only, no face required ──────────────────────────
        folder = f"users/{current_user.id}/chat"
    else:
        # ── Profile photos: parallel face + NSFW pipeline ─────────────────────
        # anchor = user's first profile photo (photos[0]). If they have none,
        # this is the first upload — DetectFaces is used instead of CompareFaces.
        existing_urls: list[str] = list(current_user.photos or [])
        anchor_url: str | None = existing_urls[0] if existing_urls else None

        # ── Crop to 1024×1024 square before duplicate check ───────────────────
        # We need to crop first so the hash matches what's already stored
        try:
            cropped_contents = await asyncio.to_thread(_crop_square_1024, contents)
        except Exception as exc:
            _log.warning("Square crop failed (using original): %s", exc)
            cropped_contents = contents

        # ── Check for duplicate photo (exact content match) ───────────────────
        duplicate_url = await _check_duplicate_photo(cropped_contents, existing_urls)
        if duplicate_url:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This photo is already in your profile. Please upload a different photo.",
            )

        # Use cropped version for analysis
        try:
            analysis = await asyncio.to_thread(analyze_photo, cropped_contents, anchor_url)
        except Exception as exc:
            _log.error("Photo analysis pipeline crashed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Photo could not be analysed. Please try a different photo.",
            )

        if not analysis.passed:
            _log.info(
                "[PHOTO REJECTED] reason=%s | nsfw=%s(%.2f) | face=%s | anchor=%.0f%% | blurry=%s",
                analysis.rejection_reason, analysis.nsfw, analysis.nsfw_score,
                analysis.has_face, analysis.anchor_match_pct, analysis.is_blurry,
            )
        else:
            _log.info(
                "[PHOTO OK] nsfw=%s(%.2f) | face=%s(age=%s) | anchor=%s(%.0f%%) | blurry=%s",
                analysis.nsfw, analysis.nsfw_score,
                analysis.has_face, analysis.age_estimate,
                analysis.anchor_checked, analysis.anchor_match_pct,
                analysis.is_blurry,
            )

        if not analysis.passed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=analysis.rejection_reason,
            )

        # ── Gender consistency check (first photo only — gender available from DetectFaces) ──
        # Threshold: 95% for hard reject, 90-95% for warning (soft fail - still allows upload)
        GENDER_CONFIDENCE_HARD = 95.0   # Hard reject above this
        GENDER_CONFIDENCE_SOFT = 90.0   # Warning below this, allow upload
        if not anchor_url and current_user.gender_id and analysis.detected_gender:
            try:
                row = await db.execute(
                    select(LookupOption.label).where(LookupOption.id == current_user.gender_id)
                )
                gender_label: str | None = row.scalar_one_or_none()
                if gender_label:
                    label_lower = gender_label.lower()
                    words = set(label_lower.split())
                    is_profile_male   = bool(words & {"male", "man", "boy"})
                    is_profile_female = bool(words & {"female", "woman", "girl"})
                    detected = analysis.detected_gender
                    conf     = analysis.gender_confidence

                    # Check for mismatch
                    is_mismatch = (
                        (is_profile_male   and detected == "Female") or
                        (is_profile_female and detected == "Male")
                    )

                    _log.warning(
                        "DEBUG GENDER | profile=%s label_lower=%s is_male=%s is_female=%s detected=%s is_mismatch=%s",
                        gender_label, label_lower, is_profile_male, is_profile_female, detected, is_mismatch,
                    )

                    # Only reject if high confidence mismatch
                    if is_mismatch and conf >= GENDER_CONFIDENCE_HARD:
                        _log.warning(
                            "Gender mismatch REJECTED | profile=%s detected=%s(%.0f%%)",
                            gender_label, detected, conf,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=(
                                "This photo doesn't match your profile gender. "
                                "Only photos that match your declared gender are allowed."
                            ),
                        )
                    elif is_mismatch and conf >= GENDER_CONFIDENCE_SOFT:
                        # Soft mismatch - log warning but allow upload
                        _log.warning(
                            "Gender mismatch ALLOWED (soft) | profile=%s detected=%s(%.0f%%)",
                            gender_label, detected, conf,
                        )
            except HTTPException:
                raise
            except Exception as exc:
                _log.warning("Gender check failed (skipping): %s", exc)

        folder = f"users/{current_user.id}/photos"
        # Use the already-cropped contents for upload
        contents = cropped_contents

    # ── Upload to DO Spaces ───────────────────────────────────────────────────
    try:
        cdn_url = await asyncio.to_thread(
            upload_photo,
            contents,
            "image/jpeg",   # always JPEG after crop
            folder=folder,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    if is_chat:
        return {"url": cdn_url}

    # ── Return URL + full analysis (profile photos) ───────────────────────────
    analysis_dict = asdict(analysis)  # type: ignore[possibly-undefined]

    return {
        "url": cdn_url,
        "analysis": {
            "passed":          analysis_dict.get("passed", True),
            "is_blurry":       analysis_dict.get("is_blurry", False),
            "blur_score":      analysis_dict.get("blur_score", 0.0),
            "has_watermark":   analysis_dict.get("has_watermark", False),
            "nsfw":            analysis_dict.get("nsfw", False),
            "nsfw_score":      analysis_dict.get("nsfw_score", 0.0),
            "has_face":        analysis_dict.get("has_face", True),
            "face_count":      analysis_dict.get("face_count", 0),
            "age_estimate":    analysis_dict.get("age_estimate"),
            "under_18_risk":   analysis_dict.get("under_18_risk", False),
            "quality_score":   analysis_dict.get("quality_score", 1.0),
            "brightness_ok":   analysis_dict.get("brightness_ok", True),
        },
    }


# ─── Transcription ────────────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    _current_user: "User" = Depends(get_current_user),
):
    """Transcribe speech audio using OpenAI Whisper. Returns {text: str}."""
    from app.core.config import settings

    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Transcription service not configured.")

    audio_bytes = await file.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")

    # Always use a name with a known extension so Whisper picks the right demuxer
    raw_name = (file.filename or "recording").split("?")[0]
    if not any(raw_name.endswith(ext) for ext in (".m4a", ".mp4", ".mp3", ".wav", ".webm", ".ogg", ".aac")):
        raw_name += ".m4a"
    filename = raw_name

    _log.info("Transcribing uploaded file: %s (%d bytes, ct=%s)", filename, len(audio_bytes), file.content_type)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                files={"file": (filename, audio_bytes, "audio/m4a")},
                data={"model": "whisper-1", "response_format": "json"},
            )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        _log.info("Transcription result (%d chars): %s", len(text), text[:120])
        return {"text": text}
    except httpx.HTTPStatusError as exc:
        _log.warning("Whisper transcription failed: %s", exc.response.text)
        raise HTTPException(status_code=502, detail="Transcription failed.")
    except Exception as exc:
        _log.warning("Whisper transcription error: %s", exc)
        raise HTTPException(status_code=500, detail="Transcription error.")


class _TranscribeUrlBody(dict):
    pass


from pydantic import BaseModel as _BM

class _TranscribeUrlPayload(_BM):
    url: str


@router.post("/transcribe-url")
async def transcribe_from_url(
    payload: _TranscribeUrlPayload,
    _current_user: "User" = Depends(get_current_user),
):
    """Download audio from a CDN URL and transcribe with Whisper. Returns {text: str}."""
    from app.core.config import settings

    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Transcription service not configured.")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            dl = await client.get(payload.url)
            dl.raise_for_status()
            audio_bytes = dl.content
    except Exception as exc:
        _log.warning("Failed to download audio for transcription: %s", exc)
        raise HTTPException(status_code=502, detail="Could not download audio.")

    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")

    # Ensure filename has a known audio extension so Whisper picks the right demuxer
    url_path = payload.url.split("?")[0]
    raw_name = url_path.split("/")[-1] or "recording"
    if not any(raw_name.endswith(ext) for ext in (".m4a", ".mp4", ".mp3", ".wav", ".webm", ".ogg", ".aac")):
        raw_name += ".m4a"
    filename = raw_name

    _log.info("Transcribing from URL: %s (%d bytes)", payload.url, len(audio_bytes))

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                files={"file": (filename, audio_bytes, "audio/m4a")},
                data={"model": "whisper-1", "response_format": "json"},
            )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        _log.info("Transcription result (%d chars): %s", len(text), text[:120])
        return {"text": text}
    except httpx.HTTPStatusError as exc:
        _log.warning("Whisper transcription failed: %s", exc.response.text)
        raise HTTPException(status_code=502, detail="Transcription failed.")
    except Exception as exc:
        _log.warning("Whisper transcription error: %s", exc)
        raise HTTPException(status_code=500, detail="Transcription error.")
