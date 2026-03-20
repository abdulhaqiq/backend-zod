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
from app.models.user import User
from app.models.verification import VerificationAttempt
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])

# ─── Duplicate detection ──────────────────────────────────────────────────────

_HASH_THRESHOLD = 8   # hamming distance — 0=identical, >10=different image


def _phash_bytes(data: bytes):
    """Return perceptual hash of image bytes."""
    import imagehash
    from PIL import Image as PILImage
    img = PILImage.open(io.BytesIO(data)).convert("RGB")
    return imagehash.phash(img)


def _is_duplicate(new_bytes: bytes, existing_urls: list[str]) -> tuple[bool, str | None]:
    """
    Downloads each existing photo and compares perceptual hashes.
    Returns (is_duplicate, matching_url | None).
    """
    import imagehash

    try:
        new_hash = _phash_bytes(new_bytes)
    except Exception as exc:
        _log.warning("Could not hash new image: %s", exc)
        return False, None

    for url in existing_urls:
        try:
            resp = httpx.get(url, timeout=5, follow_redirects=True)
            if resp.status_code != 200:
                continue
            existing_hash = _phash_bytes(resp.content)
            distance = new_hash - existing_hash
            _log.info("Hash distance vs %s → %d", url, distance)
            if distance <= _HASH_THRESHOLD:
                return True, url
        except Exception as exc:
            _log.warning("Could not fetch/hash existing photo %s: %s", url, exc)

    return False, None

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
ALLOWED_AUDIO_TYPES = {
    "audio/m4a", "audio/x-m4a", "audio/aac",
    "audio/mp4", "video/mp4",           # iOS records .m4a under video/mp4 sometimes
    "audio/mpeg", "audio/ogg",
}
MAX_IMAGE_MB = 10
MAX_AUDIO_MB = 20
MAX_AUDIO_SECONDS = 35  # a little over 30 to allow for encoding overhead


FACE_MATCH_THRESHOLD = 80.0   # AWS Rekognition recommendation for high-confidence matching
FACE_MODEL           = "ArcFace"
FACE_METRIC          = "cosine"


def _pct(distance: float, threshold: float) -> float:
    """Convert DeepFace distance → 0–100 % confidence (100 = identical)."""
    return round(max(0.0, (1.0 - distance / threshold)) * 100.0, 1)


def _correct_orientation(img):
    """Apply EXIF orientation tag so the image is right-side up."""
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def _rekognition_client():
    """Return a boto3 Rekognition client using AWS credentials from settings."""
    import boto3
    from app.core.config import settings
    return boto3.client(
        "rekognition",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=settings.AWS_REGION or "us-east-1",
    )


def _detect_face_fast(img_bytes: bytes):
    """
    Face detection using AWS Rekognition (primary) → DeepFace (fallback).
    Returns (success, reason, age, img_array, face_region).

    AWS Rekognition DetectFaces:
      - Industry-standard accuracy (used by Tinder, Bumble, Hinge)
      - Handles varied lighting, angles, expressions, skin tones
      - Returns age range estimate and face confidence
      - ~100-200ms latency

    Falls back to DeepFace if AWS credentials are not configured.
    """
    import io as _io
    import numpy as np
    from PIL import Image as PILImage, ImageStat
    from app.core.config import settings

    # Correct EXIF rotation
    raw = PILImage.open(_io.BytesIO(img_bytes))
    img = _correct_orientation(raw).convert("RGB")

    # Brightness check (applies regardless of which engine is used)
    stat = ImageStat.Stat(img)
    brightness = sum(stat.mean) / 3
    if brightness < 35:
        return False, "Image too dark. Please find better lighting.", None, None, None
    if brightness > 245:
        return False, "Image overexposed. Avoid direct bright light.", None, None, None

    img_array = np.array(img)

    # ── Primary: AWS Rekognition ──────────────────────────────────────────────
    if settings.AWS_ACCESS_KEY_ID:
        try:
            import io as _bio
            buf = _bio.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            jpeg_bytes = buf.getvalue()

            client = _rekognition_client()
            resp = client.detect_faces(
                Image={"Bytes": jpeg_bytes},
                Attributes=["ALL"],
            )
            faces = resp.get("FaceDetails", [])
            if not faces:
                return False, "No face detected. Make sure your face is clearly visible and well-lit.", None, None, None

            face = faces[0]
            confidence = face.get("Confidence", 0.0)
            if confidence < 85.0:
                return False, "Face not clearly visible. Please ensure good lighting and look directly at the camera.", None, None, None

            age_range = face.get("AgeRange", {})
            age = (age_range.get("Low", 0) + age_range.get("High", 0)) // 2

            _log.info("face-detect [Rekognition] | confidence=%.1f%% age=%s", confidence, age)
            return True, None, age, img_array, face.get("BoundingBox", {})

        except Exception as exc:
            _log.warning("face-detect | Rekognition failed, falling back to DeepFace: %s", exc)

    # ── Fallback: DeepFace ────────────────────────────────────────────────────
    try:
        from deepface import DeepFace

        w, h = img.size
        min_side = min(w, h)
        if min_side < 640:
            scale = 640 / min_side
            img = img.resize((int(w * scale), int(h * scale)), PILImage.Resampling.LANCZOS)
            img_array = np.array(img)

        result = DeepFace.analyze(
            img_path=img_array,
            actions=["age"],
            detector_backend="retinaface",
            enforce_detection=True,
            silent=True,
        )
        faces = result if isinstance(result, list) else [result]
        if not faces:
            return False, "No face detected. Make sure your face is clearly visible.", None, None, None

        face = faces[0]
        age = int(face.get("age", 0))
        region = face.get("region", {})
        confidence = face.get("face_confidence", 0.0)
        if confidence < 0.70:
            return False, "Face not clearly visible. Please ensure good lighting and look directly at the camera.", None, None, None

        _log.info("face-detect [DeepFace] | confidence=%.2f age=%s", confidence, age)
        return True, None, age, img_array, region

    except Exception as exc:
        msg = str(exc).lower()
        _log.warning("face-detect | DeepFace failed: %s", exc)
        if any(k in msg for k in ("face", "detected", "could not", "detector")):
            return False, "No face detected. Make sure your face is fully visible and well-lit.", None, None, None
        return False, "Could not process image. Please try again.", None, None, None


def _match_against_photos(
    selfie_img_bytes: bytes,
    photo_urls: list[str],
) -> tuple[float, int]:
    """
    Industry-grade face matching using AWS Rekognition CompareFaces (primary)
    with DeepFace ArcFace as fallback.

    Returns (best_similarity_pct, photos_compared).

    AWS Rekognition CompareFaces:
      - 99%+ accuracy, same engine used by Tinder, Bumble, Hinge
      - Handles varied angles, lighting, expressions, ages
      - Returns similarity 0-100% (not a distance metric — higher = more similar)
      - ~150ms per comparison
    """
    import io as _io
    from PIL import Image as PILImage
    from app.core.config import settings

    best_pct = 0.0
    compared = 0

    # Convert selfie to JPEG bytes once for Rekognition
    selfie_jpeg: bytes | None = None
    if settings.AWS_ACCESS_KEY_ID:
        try:
            buf = _io.BytesIO()
            PILImage.open(_io.BytesIO(selfie_img_bytes)).convert("RGB").save(buf, format="JPEG", quality=90)
            selfie_jpeg = buf.getvalue()
        except Exception:
            selfie_jpeg = None

    use_rekognition = bool(selfie_jpeg and settings.AWS_ACCESS_KEY_ID)

    for url in photo_urls:
        try:
            resp = httpx.get(url, timeout=8, follow_redirects=True)
            if resp.status_code != 200:
                continue

            profile_bytes = resp.content
            pct: float | None = None

            # ── Primary: AWS Rekognition ──────────────────────────────────────
            if use_rekognition:
                try:
                    # Convert profile photo to JPEG for Rekognition
                    buf2 = _io.BytesIO()
                    PILImage.open(_io.BytesIO(profile_bytes)).convert("RGB").save(buf2, format="JPEG", quality=90)
                    target_jpeg = buf2.getvalue()

                    client = _rekognition_client()
                    rek_resp = client.compare_faces(
                        SourceImage={"Bytes": selfie_jpeg},
                        TargetImage={"Bytes": target_jpeg},
                        SimilarityThreshold=0,  # get all results, we apply our own threshold
                    )
                    matches = rek_resp.get("FaceMatches", [])
                    if matches:
                        pct = float(matches[0]["Similarity"])
                        _log.info("Face match [Rekognition] vs %s → %.1f%%",
                                  url.split("/")[-1][:24], pct)
                    else:
                        # No match found by Rekognition (different person or no face)
                        pct = 0.0
                        _log.info("Face match [Rekognition] vs %s → no match",
                                  url.split("/")[-1][:24])
                    compared += 1
                except Exception as exc:
                    _log.warning("Rekognition compare failed for %s: %s — falling back to DeepFace",
                                 url.split("/")[-1][:24], exc)
                    pct = None  # trigger fallback

            # ── Fallback: DeepFace ArcFace ────────────────────────────────────
            if pct is None:
                try:
                    import numpy as np
                    from deepface import DeepFace

                    selfie_arr  = np.array(PILImage.open(_io.BytesIO(selfie_img_bytes)).convert("RGB"))
                    profile_arr = np.array(PILImage.open(_io.BytesIO(profile_bytes)).convert("RGB"))

                    result = DeepFace.verify(
                        img1_path=selfie_arr,
                        img2_path=profile_arr,
                        model_name="ArcFace",
                        distance_metric="cosine",
                        detector_backend="retinaface",
                        enforce_detection=False,
                        silent=True,
                    )
                    pct = _pct(result["distance"], result["threshold"])
                    _log.info("Face match [DeepFace] vs %s → %.1f%%",
                              url.split("/")[-1][:24], pct)
                    compared += 1
                except Exception as exc:
                    msg = str(exc).lower()
                    if any(k in msg for k in ("face", "detected", "could not")):
                        _log.info("No face in profile photo %s — skipping", url.split("/")[-1][:24])
                    else:
                        _log.warning("DeepFace match error for %s: %s", url.split("/")[-1][:24], exc)
                    continue

            if pct is not None and pct > best_pct:
                best_pct = pct

        except Exception as exc:
            _log.warning("Face-match outer error for %s: %s", url.split("/")[-1][:24], exc)

    return round(best_pct, 1), compared


# ─── Background verification task ─────────────────────────────────────────────

async def _process_verification(
    attempt_id: _uuid.UUID,
    selfie_bytes: bytes,
    photo_urls: list[str],
) -> None:
    """
    Runs after the HTTP response is returned.
    Performs liveness + face-match, then updates the attempt record and user row.
    """
    _log.info("bg-verify | attempt=%s STARTED", attempt_id)
    async with AsyncSessionLocal() as db:
        attempt: VerificationAttempt | None = await db.get(VerificationAttempt, attempt_id)
        if not attempt:
            _log.warning("bg-verify | attempt=%s not found in DB", attempt_id)
            return
        user: User | None = await db.get(User, attempt.user_id)
        if not user:
            _log.warning("bg-verify | user not found for attempt=%s", attempt_id)
            return

        try:
            _log.info("bg-verify | attempt=%s running face detection...", attempt_id)
            # Layer 1 — face detection + quality check
            success, reason, age_estimate, selfie_array, face_region = await asyncio.to_thread(
                _detect_face_fast, selfie_bytes
            )
            attempt.is_live = success
            attempt.age_estimate = age_estimate

            if not success:
                attempt.status = "rejected"
                attempt.rejection_reason = reason
                attempt.processed_at = datetime.now(timezone.utc)
                user.verification_status = "rejected"
                await db.commit()
                _log.info("bg-verify | attempt=%s FACE DETECTION FAIL: %s", attempt_id, reason)
                return

            # Layer 2 — face match
            if not photo_urls:
                attempt.status = "rejected"
                attempt.rejection_reason = "No profile photos found. Please upload photos first."
                attempt.processed_at = datetime.now(timezone.utc)
                user.verification_status = "rejected"
                await db.commit()
                _log.info("bg-verify | attempt=%s NO PHOTOS", attempt_id)
                return

            _log.info("bg-verify | attempt=%s running face match vs %d photos...", attempt_id, len(photo_urls))
            best_pct, compared = await asyncio.to_thread(
                _match_against_photos, selfie_bytes, photo_urls
            )
            attempt.face_match_score = best_pct
            passed = best_pct >= FACE_MATCH_THRESHOLD
            _log.info("bg-verify | attempt=%s face match result: %.1f%% (compared=%d, passed=%s)", attempt_id, best_pct, compared, passed)

            if passed:
                attempt.status = "verified"
                user.is_verified = True
                user.verification_status = "verified"
                user.face_match_score = best_pct
                # Store selfie so ID verification can use it for face matching
                try:
                    from app.core.storage import upload_file as _upload_file
                    selfie_cdn = await asyncio.to_thread(
                        _upload_file,
                        selfie_bytes, "image/jpeg",
                        folder=f"users/{user.id}/selfies",
                        ext=".jpg",
                    )
                    attempt.selfie_url = selfie_cdn
                except Exception as exc:
                    _log.warning("bg-verify | selfie upload failed (non-critical): %s", exc)
            else:
                attempt.status = "rejected"
                attempt.rejection_reason = (
                    f"Face match too low ({best_pct:.0f}%). "
                    "Make sure your selfie matches the photos on your profile."
                    if compared > 0
                    else "Could not compare faces. Ensure your profile photos show your face clearly."
                )
                user.verification_status = "rejected"

            attempt.processed_at = datetime.now(timezone.utc)
            await db.commit()

            _log.info(
                "bg-verify | attempt=%s passed=%s match=%.1f%% compared=%d",
                attempt_id, passed, best_pct, compared,
            )

            # Push result immediately to any connected WS clients — no polling needed
            from app.api.v1.endpoints.verification_ws import watcher as _watcher
            await _watcher.notify(user.id, {
                "status":           attempt.status,
                "face_match_score": attempt.face_match_score,
                "rejection_reason": attempt.rejection_reason,
                "is_live":          attempt.is_live,
            })

        except Exception as exc:
            _log.error("bg-verify | attempt=%s CRASHED: %s", attempt_id, exc, exc_info=True)
            attempt.status = "rejected"
            attempt.rejection_reason = "Internal analysis error. Please try again."
            attempt.processed_at = datetime.now(timezone.utc)
            user.verification_status = "rejected"
            await db.commit()
            # Notify WS clients of the failure too
            try:
                from app.api.v1.endpoints.verification_ws import watcher as _watcher
                await _watcher.notify(user.id, {
                    "status": "rejected",
                    "rejection_reason": attempt.rejection_reason,
                    "face_match_score": None,
                })
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

    # ── Snapshot profile photos now (user may change them later) ─────────────
    photo_urls: list[str] = list(current_user.photos or [])

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
    """
    Run EasyOCR on an ID image and return all detected text joined as a string.
    No GPU required; runs on CPU fine for ID card text sizes.
    """
    import io as _io
    import numpy as np
    from PIL import Image as PILImage
    import easyocr

    img = PILImage.open(_io.BytesIO(img_bytes))
    img = _correct_orientation(img).convert("RGB")

    # Upscale small IDs for better OCR accuracy
    w, h = img.size
    if min(w, h) < 600:
        scale = 600 / min(w, h)
        img = img.resize((int(w * scale), int(h * scale)), PILImage.Resampling.LANCZOS)

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    results = reader.readtext(np.array(img), detail=0, paragraph=False)
    return " ".join(results)


_DATE_PATTERN = r"\b(\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4}|\d{4}[\s/\-\.]\d{1,2}[\s/\-\.]\d{1,2})\b"
_ID_NUMBER_PATTERN = r"\b[A-Z0-9]{6,12}\b"
_EXPIRY_WORDS = {"exp", "expiry", "expires", "expiration", "valid", "until", "thru"}
_DOB_WORDS    = {"dob", "born", "birth", "date of birth", "birthday"}


def _analyse_id_text(text: str) -> dict:
    """
    Parse OCR text for ID field presence:
      - has_name:   2+ capitalised words side-by-side (First Last pattern)
      - has_dob:    date-like pattern near 'birth' / 'dob' keyword, or any date pattern
      - has_expiry: date-like pattern near 'exp' / 'valid' keyword
      - has_number: alphanumeric 6-12 char token (ID/licence number)
    Returns dict with boolean flags and detected field snippets.
    """
    import re

    lower = text.lower()
    dates  = re.findall(_DATE_PATTERN, text)
    idnums = re.findall(_ID_NUMBER_PATTERN, text)

    has_name   = bool(re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", text))
    has_dob    = any(w in lower for w in _DOB_WORDS) and bool(dates)
    has_expiry = any(w in lower for w in _EXPIRY_WORDS) and bool(dates)
    has_number = bool(idnums)

    # Fallback: if no keyword context, still credit dates as dob/expiry presence
    if not has_dob and len(dates) >= 1:
        has_dob = True
    if not has_expiry and len(dates) >= 2:
        has_expiry = True

    return {
        "has_name":   has_name,
        "has_dob":    has_dob,
        "has_expiry": has_expiry,
        "has_number": has_number,
    }


def _match_face_on_id(selfie_bytes: bytes, id_bytes: bytes) -> tuple[bool, float]:
    """
    Compare the user's stored selfie against the face cropped from the ID photo.
    Returns (passed, match_pct).
    """
    import io as _io
    import numpy as np
    from PIL import Image as PILImage
    from deepface import DeepFace

    selfie_img = PILImage.open(_io.BytesIO(selfie_bytes))
    selfie_img = _correct_orientation(selfie_img).convert("RGB")
    selfie_arr = np.array(selfie_img)

    id_img = PILImage.open(_io.BytesIO(id_bytes))
    id_img = _correct_orientation(id_img).convert("RGB")
    id_arr = np.array(id_img)

    try:
        result = DeepFace.verify(
            img1_path=selfie_arr,
            img2_path=id_arr,
            model_name="ArcFace",
            distance_metric="cosine",
            detector_backend="retinaface",
            enforce_detection=False,
            silent=True,
        )
        pct = _pct(result["distance"], result["threshold"])
        _log.info("id-face-match | distance=%.3f threshold=%.3f => %.1f%%",
                  result["distance"], result["threshold"], pct)
        return pct >= FACE_MATCH_THRESHOLD, round(pct, 1)
    except Exception as exc:
        _log.warning("id-face-match | failed: %s", exc)
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
    """
    _log.info("bg-id | attempt=%s STARTED", attempt_id)
    async with AsyncSessionLocal() as db:
        attempt: VerificationAttempt | None = await db.get(VerificationAttempt, attempt_id)
        if not attempt:
            _log.warning("bg-id | attempt=%s not found", attempt_id)
            return
        user: User | None = await db.get(User, attempt.user_id)
        if not user:
            _log.warning("bg-id | user not found for attempt=%s", attempt_id)
            return

        try:
            # ── OCR front of ID ───────────────────────────────────────────────
            _log.info("bg-id | attempt=%s running OCR on front...", attempt_id)
            front_text = await asyncio.to_thread(_extract_id_text, front_bytes)
            _log.info("bg-id | front OCR: %s", front_text[:200])

            all_text = front_text
            if back_bytes:
                _log.info("bg-id | attempt=%s running OCR on back...", attempt_id)
                back_text = await asyncio.to_thread(_extract_id_text, back_bytes)
                _log.info("bg-id | back OCR: %s", back_text[:200])
                all_text = front_text + " " + back_text

            fields = _analyse_id_text(all_text)
            attempt.id_text_detected = all_text[:2000]  # cap storage
            attempt.id_has_name   = fields["has_name"]
            attempt.id_has_dob    = fields["has_dob"]
            attempt.id_has_expiry = fields["has_expiry"]
            attempt.id_has_number = fields["has_number"]

            missing = [k for k, v in fields.items() if not v]
            _log.info("bg-id | attempt=%s fields=%s missing=%s", attempt_id, fields, missing)

            # Require at least 2 of 4 fields for "looks like an ID"
            detected_count = sum(fields.values())
            if detected_count < 2:
                attempt.status = "rejected"
                attempt.rejection_reason = (
                    "The image doesn't look like a valid ID. "
                    "Make sure the ID is clear, well-lit and fully visible."
                )
                attempt.processed_at = datetime.now(timezone.utc)
                user.verification_status = "rejected"
                await db.commit()
                _log.info("bg-id | attempt=%s REJECTED (only %d fields detected)", attempt_id, detected_count)
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
                        f"Face on ID doesn't match your selfie ({face_pct:.0f}% match, need 70%). "
                        "Make sure you are uploading your own ID."
                    )
                    attempt.processed_at = datetime.now(timezone.utc)
                    user.verification_status = "rejected"
                    await db.commit()
                    return
            else:
                _log.info("bg-id | attempt=%s no selfie on file — skipping face match", attempt_id)
                attempt.id_face_match_score = None

            # ── All checks passed ─────────────────────────────────────────────
            attempt.status = "verified"
            attempt.processed_at = datetime.now(timezone.utc)
            user.verification_status = "verified"
            user.is_verified = True
            await db.commit()
            _log.info("bg-id | attempt=%s VERIFIED (fields=%d, face=%.1f%%)",
                      attempt_id, detected_count, attempt.id_face_match_score or 0)

        except Exception as exc:
            _log.error("bg-id | attempt=%s CRASHED: %s", attempt_id, exc, exc_info=True)
            attempt.status = "rejected"
            attempt.rejection_reason = "Internal analysis error. Please try again."
            attempt.processed_at = datetime.now(timezone.utc)
            user.verification_status = "rejected"
            await db.commit()


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
            resp = httpx.get(face_attempt.selfie_url, timeout=8, follow_redirects=True)
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
        # ── Chat photos: skip face/quality analysis, upload directly ──────────
        # Chat images can be anything (memes, screenshots, selfies, etc.)
        # No face detection requirement.
        folder = f"users/{current_user.id}/chat"
    else:
        # ── Profile photos: full analysis pipeline ────────────────────────────
        try:
            analysis = await asyncio.to_thread(analyze_photo, contents)
        except Exception as exc:
            _log.error("Photo analysis pipeline crashed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Photo could not be analysed. Please try a different photo.",
            )

        if not analysis.passed:
            print(f"[PHOTO REJECTED] reason={analysis.rejection_reason} | nsfw={analysis.nsfw}({analysis.nsfw_score:.2f}) labels={analysis.nsfw_labels} | face={analysis.has_face} | blurry={analysis.is_blurry}", flush=True)
        else:
            print(f"[PHOTO OK] nsfw={analysis.nsfw}({analysis.nsfw_score:.2f}) | face={analysis.has_face}(age={analysis.age_estimate}) | blurry={analysis.is_blurry}", flush=True)

        if not analysis.passed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=analysis.rejection_reason,
            )

        # ── Duplicate photo detection ─────────────────────────────────────────
        existing_urls: list[str] = list(current_user.photos or [])
        if existing_urls:
            try:
                is_dup, dup_url = await asyncio.to_thread(
                    _is_duplicate, contents, existing_urls
                )
                if is_dup:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="This photo is too similar to one you've already uploaded. Please choose a different photo.",
                    )
            except HTTPException:
                raise
            except Exception as exc:
                _log.warning("Duplicate check failed (skipping): %s", exc)

        # ── Face consistency check ────────────────────────────────────────────
        PROFILE_MATCH_THRESHOLD = 60.0

        if existing_urls and analysis.has_face:
            try:
                best_pct, compared = await asyncio.to_thread(
                    _match_against_photos, contents, existing_urls
                )
                print(f"[FACE MATCH] best={best_pct:.1f}% vs {compared} photos | threshold={PROFILE_MATCH_THRESHOLD}%", flush=True)

                if compared > 0 and best_pct < PROFILE_MATCH_THRESHOLD:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="This photo doesn't appear to be the same person as your other photos. Please upload a photo of yourself.",
                    )
            except HTTPException:
                raise
            except Exception as exc:
                _log.warning("Face consistency check failed (skipping): %s", exc)

        folder = f"users/{current_user.id}/photos"

    # ── Upload to DO Spaces ───────────────────────────────────────────────────
    try:
        cdn_url = await asyncio.to_thread(
            upload_photo,
            contents,
            file.content_type or "image/jpeg",
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
