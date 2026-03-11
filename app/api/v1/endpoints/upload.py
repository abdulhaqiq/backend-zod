"""
Upload endpoints:
  POST /upload/photo  — analyze + upload a photo to DO Spaces, returns CDN URL + analysis
  POST /upload/audio  — upload a voice clip (m4a/aac/mp4) to DO Spaces, returns CDN URL
"""
import asyncio
import logging
from dataclasses import asdict

import io
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.deps import get_current_user
from app.core.photo_analyzer import analyze_photo
from app.core.storage import upload_file, upload_photo
from app.models.user import User

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


@router.post("/audio", summary="Upload a 30-second voice prompt to DigitalOcean Spaces")
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
    current_user: User = Depends(get_current_user),
):
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

    # ── Run photo analysis (NSFW + face + quality) in thread ─────────────────
    try:
        analysis = await asyncio.to_thread(analyze_photo, contents)
    except Exception as exc:
        # Analysis pipeline itself crashed — fail CLOSED (reject, never skip checks)
        _log.error("Photo analysis pipeline crashed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Photo could not be analysed. Please try a different photo.",
        )

    _log.info(
        "Analysis result | passed=%s blurry=%s(%.0f) watermark=%s "
        "nsfw=%s(%.2f) face=%s(n=%s age=%s) rejected=%s",
        analysis.passed,
        analysis.is_blurry, analysis.blur_score,
        analysis.has_watermark,
        analysis.nsfw, analysis.nsfw_score,
        analysis.has_face, analysis.face_count, analysis.age_estimate,
        analysis.rejection_reason or "—",
    )

    # Hard reject on any failed check
    if not analysis.passed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=analysis.rejection_reason,
        )

    # ── Duplicate photo detection (perceptual hash) ───────────────────────────
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

    # ── Upload to DO Spaces ───────────────────────────────────────────────────
    try:
        cdn_url = await asyncio.to_thread(
            upload_photo,
            contents,
            file.content_type or "image/jpeg",
            folder=f"users/{current_user.id}/photos",
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    # ── Return URL + full analysis ────────────────────────────────────────────
    analysis_dict = asdict(analysis)

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
