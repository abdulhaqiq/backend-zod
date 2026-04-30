"""
Photo analysis pipeline — AWS Rekognition only.

Two-call parallel pipeline:
  ┌─────────────────────────────────────────────────────────┐
  │ First photo   → DetectFaces(ALL) ║ DetectModerationLabels│
  │ Next photos   → CompareFaces     ║ DetectModerationLabels│
  └─────────────────────────────────────────────────────────┘

Both calls run in parallel via ThreadPoolExecutor — no sequential waiting.
CompareFaces against the user's first/anchor photo replaces DetectFaces for
all subsequent uploads, giving face-match + existence check in one shot.
"""

import io
import logging
from concurrent.futures import ThreadPoolExecutor, wait as _wait, FIRST_EXCEPTION
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

SHARPNESS_MIN         = 10.0   # Quality.Sharpness below → blurry
BRIGHTNESS_MIN        =  5.0   # Quality.Brightness below → too dark
BRIGHTNESS_MAX        = 98.0   # Quality.Brightness above → overexposed
MIN_AGE_ALLOWED       = 18
FACE_CONFIDENCE       = 70.0   # Minimum DetectFaces confidence
ANCHOR_MATCH_MIN      = 40.0   # CompareFaces similarity % — same person required

NSFW_REJECT_LABELS = {
    "Explicit Nudity", "Nudity", "Graphic Male Nudity", "Graphic Female Nudity",
    "Nude Male", "Nude Female", "Explicit Sexual Activity", "Graphic Sexual Activity",
    "Partial Nudity", "Exposed Male Genitalia", "Exposed Female Genitalia",
    "Exposed Anus", "Exposed Buttocks Or Anus",
}


# ── Rekognition client ────────────────────────────────────────────────────────

def _client():
    import boto3
    from botocore.config import Config
    from app.core.config import settings
    return boto3.client(
        "rekognition",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=getattr(settings, "AWS_REGION", None) or "us-east-1",
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
    )


# ── JPEG conversion ───────────────────────────────────────────────────────────

_REKOGNITION_MAX_BYTES = 5 * 1024 * 1024   # 5 MB hard limit
_JPEG_MAX_DIMENSION   = 1920               # resize long-edge to this before quality reduction

# In-memory cache for anchor photos (avoids re-downloading on every upload)
_ANCHOR_CACHE: dict[str, bytes] = {}
_ANCHOR_CACHE_MAX_SIZE = 100  # Keep last 100 anchor photos in memory


def _to_jpeg(img_bytes: bytes) -> bytes:
    """
    Convert any supported format (JPEG/PNG/WebP/HEIC) to JPEG for Rekognition.
    Ensures output is under Rekognition's 5 MB limit by:
      1. Resizing the long edge to 1920 px (enough detail, drastically reduces file size)
      2. Iteratively lowering quality (80 → 60 → 40) until under 5 MB
    """
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

    # Resize long edge to 1920 px while keeping aspect ratio
    w, h = img.size
    if max(w, h) > _JPEG_MAX_DIMENSION:
        scale = _JPEG_MAX_DIMENSION / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Try progressively lower quality until under 5 MB (reduced iterations for speed)
    for quality in (80, 60, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= _REKOGNITION_MAX_BYTES:
            return buf.getvalue()

    # Last resort: halve dimensions again and save at quality 40
    w2, h2 = img.size
    img = img.resize((w2 // 2, h2 // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=40, optimize=True)
    return buf.getvalue()


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class PhotoAnalysis:
    passed: bool
    rejection_reason: Optional[str]

    # Quality
    is_blurry: bool
    sharpness: float
    brightness_ok: bool
    brightness: float
    quality_score: float

    # NSFW
    nsfw: bool
    nsfw_score: float
    nsfw_labels: list = field(default_factory=list)

    # Face
    has_face: bool = False
    face_count: int = 0
    multiple_faces: bool = False
    age_estimate: Optional[int] = None
    under_18_risk: bool = False
    detected_gender: Optional[str] = None
    gender_confidence: float = 0.0

    # Anchor match (only for subsequent photos)
    anchor_match_pct: float = 0.0
    anchor_checked: bool = False

    # Watermark field kept for API compatibility but always False now
    has_watermark: bool = False
    watermark_text: str = ""


# ── Call 1a: DetectFaces (first photo) ───────────────────────────────────────

def _detect_faces(jpeg_bytes: bytes):
    """
    Used for the very first photo — no anchor to compare against yet.
    Returns (has_face, face_count, age_estimate, under_18_risk,
             is_blurry, sharpness, brightness_ok, brightness, quality_score,
             detected_gender, gender_confidence)
    """
    try:
        resp  = _client().detect_faces(Image={"Bytes": jpeg_bytes}, Attributes=["ALL"])
        faces = [f for f in resp.get("FaceDetails", []) if f.get("Confidence", 0) >= FACE_CONFIDENCE]

        if not faces:
            # Fallback: DetectLabels to allow standing / full-body shots
            try:
                lresp = _client().detect_labels(
                    Image={"Bytes": jpeg_bytes}, MaxLabels=10, MinConfidence=80.0
                )
                if any(
                    l["Name"] in ("Person", "Human", "People") and l["Confidence"] >= 80.0
                    for l in lresp.get("Labels", [])
                ):
                    logger.info("DetectFaces: no close face — person detected (standing/full-body)")
                    return True, 1, None, False, False, 60.0, True, 50.0, 0.6, None, 0.0
            except Exception as e:
                logger.warning("Person label fallback failed: %s", e)

            logger.info("DetectFaces: no face detected")
            return False, 0, None, False, False, 0.0, True, 50.0, 0.0, None, 0.0

        face      = faces[0]
        quality   = face.get("Quality", {})
        sharpness = float(quality.get("Sharpness", 50.0))
        brightness = float(quality.get("Brightness", 50.0))

        is_blurry     = sharpness < SHARPNESS_MIN
        brightness_ok = BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX
        quality_score = round((sharpness / 100.0) * 0.7 + (1.0 if brightness_ok else 0.3) * 0.3, 3)

        ages       = [(f["AgeRange"]["Low"] + f["AgeRange"]["High"]) // 2 for f in faces if "AgeRange" in f]
        avg_age    = int(sum(ages) / len(ages)) if ages else None
        min_age    = min(ages) if ages else None
        under_18   = min_age is not None and min_age < MIN_AGE_ALLOWED

        gender_data = face.get("Gender", {})
        det_gender  = gender_data.get("Value")
        gender_conf = float(gender_data.get("Confidence", 0.0))

        logger.info(
            "DetectFaces | faces=%d sharpness=%.1f brightness=%.1f ages=%s under18=%s gender=%s(%.0f%%)",
            len(faces), sharpness, brightness, ages, under_18, det_gender, gender_conf,
        )
        return True, len(faces), avg_age, under_18, is_blurry, sharpness, brightness_ok, brightness, quality_score, det_gender, gender_conf

    except Exception as exc:
        logger.warning("DetectFaces failed: %s", exc)
        return False, 0, None, False, False, 0.0, True, 50.0, 0.0, None, 0.0


# ── Call 1b: CompareFaces against anchor (subsequent photos) ─────────────────

def _compare_with_anchor(new_jpeg: bytes, anchor_url: str):
    """
    Used for every photo after the first.
    Downloads the anchor (first profile photo) and calls CompareFaces.
    Returns (has_face, similarity_pct, is_blurry, sharpness, brightness_ok, brightness, quality_score)
    """
    try:
        # Check cache first to avoid re-downloading the same anchor
        if anchor_url in _ANCHOR_CACHE:
            anchor_jpeg = _ANCHOR_CACHE[anchor_url]
            logger.debug("CompareFaces: using cached anchor")
        else:
            # Download anchor with reduced timeout
            resp = httpx.get(anchor_url, timeout=4, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning("CompareFaces: could not fetch anchor (%d)", resp.status_code)
                return False, 0.0, False, 0.0, True, 50.0, 0.0, False

            anchor_jpeg = _to_jpeg(resp.content)
            
            # Cache it (LRU: remove oldest if cache is full)
            if len(_ANCHOR_CACHE) >= _ANCHOR_CACHE_MAX_SIZE:
                # Remove the first (oldest) entry
                _ANCHOR_CACHE.pop(next(iter(_ANCHOR_CACHE)))
            _ANCHOR_CACHE[anchor_url] = anchor_jpeg
            logger.debug("CompareFaces: cached new anchor")

        rek = _client().compare_faces(
            SourceImage={"Bytes": anchor_jpeg},
            TargetImage={"Bytes": new_jpeg},
            SimilarityThreshold=0,
        )

        matches   = rek.get("FaceMatches", [])
        unmatched = rek.get("UnmatchedFaces", [])

        if not matches:
            if unmatched:
                total_faces = len(unmatched)
                logger.info("CompareFaces: face found but doesn't match anchor (0%%) faces=%d", total_faces)
            else:
                # No faces detected — try DetectLabels fallback for standing/full-body shots
                logger.info("CompareFaces: no face found in new photo, trying person detection fallback...")
                try:
                    lresp = _client().detect_labels(
                        Image={"Bytes": new_jpeg}, MaxLabels=10, MinConfidence=80.0
                    )
                    if any(
                        l["Name"] in ("Person", "Human", "People") and l["Confidence"] >= 80.0
                        for l in lresp.get("Labels", [])
                    ):
                        logger.info("CompareFaces: no close face but person detected (standing/full-body) — accepting")
                        # Accept as valid but with low similarity (triggers warning but doesn't reject)
                        return True, 45.0, False, 50.0, True, 50.0, 0.6, False
                except Exception as e:
                    logger.warning("CompareFaces: person label fallback failed: %s", e)
                
                logger.info("CompareFaces: no face or person found in new photo")
            # Return multiple_faces flag as last element
            return bool(unmatched), 0.0, False, 50.0, True, 50.0, 0.5, len(unmatched) > 1

        best      = max(matches, key=lambda m: m["Similarity"])
        sim_pct   = float(best["Similarity"])
        face_det  = best.get("Face", {})
        quality   = face_det.get("Quality", {})
        sharpness = float(quality.get("Sharpness", 50.0))
        brightness = float(quality.get("Brightness", 50.0))

        is_blurry     = sharpness < SHARPNESS_MIN
        brightness_ok = BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX
        quality_score = round((sharpness / 100.0) * 0.7 + (1.0 if brightness_ok else 0.3) * 0.3, 3)

        # Multiple people = matched face + any unmatched faces in the same photo
        total_faces    = len(matches) + len(unmatched)
        multiple_faces = total_faces > 1

        logger.info(
            "CompareFaces | similarity=%.1f%% sharpness=%.1f brightness=%.1f faces=%d",
            sim_pct, sharpness, brightness, total_faces,
        )
        return True, sim_pct, is_blurry, sharpness, brightness_ok, brightness, quality_score, multiple_faces

    except Exception as exc:
        logger.warning("CompareFaces failed: %s", exc)
        return False, 0.0, False, 0.0, True, 50.0, 0.0, False


# ── Call 2: DetectModerationLabels (always, runs in parallel) ────────────────

def _check_nsfw(jpeg_bytes: bytes):
    try:
        resp  = _client().detect_moderation_labels(Image={"Bytes": jpeg_bytes}, MinConfidence=50.0)
        labels = resp.get("ModerationLabels", [])
        hits   = [l for l in labels if l["Name"] in NSFW_REJECT_LABELS]

        if hits:
            top_score   = max(l["Confidence"] for l in hits)
            label_names = [l["Name"] for l in hits]
            logger.info("NSFW detected: %s", label_names)
            return True, round(top_score / 100.0, 4), label_names

        all_scores = [l["Confidence"] for l in labels]
        return False, round(max(all_scores) / 100.0, 4) if all_scores else 0.0, []

    except Exception as exc:
        logger.warning("NSFW check failed: %s", exc)
        return False, 0.0, []


# ── Quality score helper (used for photo reordering) ─────────────────────────

def get_photo_quality_score(img_bytes: bytes) -> float:
    try:
        jpeg_bytes = _to_jpeg(img_bytes)
        resp  = _client().detect_faces(Image={"Bytes": jpeg_bytes}, Attributes=["ALL"])
        faces = [f for f in resp.get("FaceDetails", []) if f.get("Confidence", 0) >= FACE_CONFIDENCE]
        if not faces:
            return 0.0
        quality    = faces[0].get("Quality", {})
        sharpness  = float(quality.get("Sharpness", 0.0))
        brightness = float(quality.get("Brightness", 50.0))
        brightness_ok = BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX
        return round((sharpness / 100.0) * 0.7 + (1.0 if brightness_ok else 0.3) * 0.3, 3)
    except Exception as exc:
        logger.warning("Quality score failed: %s", exc)
        return 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_photo(img_bytes: bytes, anchor_url: Optional[str] = None) -> PhotoAnalysis:
    """
    Run face + NSFW analysis in parallel.

    anchor_url — URL of the user's first/anchor profile photo.
      • None  → first photo: DetectFaces  ║ DetectModerationLabels (parallel)
      • str   → next photos: CompareFaces ║ DetectModerationLabels (parallel)

    Hard rejections:
      ✗ No face / person detected
      ✗ Blurry (sharpness < 10)
      ✗ Too dark / overexposed
      ✗ Explicit/adult NSFW content
      ✗ Appears under 18 (first-photo only)
      ✗ Face doesn't match anchor (subsequent photos, similarity < 40%)
    """
    try:
        jpeg_bytes = _to_jpeg(img_bytes)
    except Exception as exc:
        logger.error("Image conversion failed: %s", exc)
        return PhotoAnalysis(
            passed=False,
            rejection_reason="Could not read image. Please try a different photo.",
            is_blurry=False, sharpness=0.0, brightness_ok=True, brightness=50.0, quality_score=0.0,
            nsfw=False, nsfw_score=0.0,
            has_face=False, face_count=0, multiple_faces=False, age_estimate=None, under_18_risk=False,
        )

    # ── Run both checks in parallel ───────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=2) as pool:
        nsfw_future = pool.submit(_check_nsfw, jpeg_bytes)

        if anchor_url:
            face_future = pool.submit(_compare_with_anchor, jpeg_bytes, anchor_url)
        else:
            face_future = pool.submit(_detect_faces, jpeg_bytes)

    nsfw, nsfw_score, nsfw_labels = nsfw_future.result()

    # ── Unpack face results ───────────────────────────────────────────────────
    anchor_match_pct = 0.0
    anchor_checked   = False
    detected_gender  = None
    gender_confidence = 0.0
    age_estimate     = None
    under_18_risk    = False
    face_count       = 0
    multiple_faces   = False

    if anchor_url:
        anchor_checked = True
        (has_face, anchor_match_pct,
         is_blurry, sharpness, brightness_ok, brightness, quality_score,
         multiple_faces) = face_future.result()
    else:
        (has_face, face_count, age_estimate, under_18_risk,
         is_blurry, sharpness, brightness_ok, brightness, quality_score,
         detected_gender, gender_confidence) = face_future.result()
        multiple_faces = face_count > 1

    # ── Apply rejections in priority order ────────────────────────────────────
    rejection_reason: Optional[str] = None

    if not has_face:
        rejection_reason = "No face detected. Please upload a clear photo showing your face."
    elif multiple_faces:
        rejection_reason = "Multiple people detected. Please upload a solo photo of yourself."
    elif is_blurry:
        rejection_reason = "Photo is too blurry. Please upload a sharper, clearer photo."
    elif not brightness_ok:
        rejection_reason = (
            "Photo is too dark. Please find better lighting."
            if brightness < BRIGHTNESS_MIN
            else "Photo is overexposed. Please avoid direct bright light."
        )
    elif nsfw:
        rejection_reason = "Photo contains explicit or adult content and cannot be uploaded."
    elif under_18_risk:
        rejection_reason = "Photo appears to show someone under 18. Upload rejected."
    elif anchor_checked and anchor_match_pct < ANCHOR_MATCH_MIN:
        rejection_reason = (
            "This photo doesn't appear to be the same person as your other photos. "
            "Please upload a photo of yourself."
        )

    passed = rejection_reason is None

    logger.info(
        "Photo analysis | passed=%s face=%s anchor=%s(%.0f%%) sharpness=%.0f brightness=%.0f nsfw=%s(%.2f)%s",
        passed, has_face,
        anchor_checked, anchor_match_pct,
        sharpness, brightness,
        nsfw, nsfw_score,
        f" | REJECTED: {rejection_reason}" if not passed else "",
    )

    return PhotoAnalysis(
        passed=passed,
        rejection_reason=rejection_reason,
        is_blurry=is_blurry,
        sharpness=sharpness,
        brightness_ok=brightness_ok,
        brightness=brightness,
        quality_score=quality_score,
        has_watermark=False,
        watermark_text="",
        nsfw=nsfw,
        nsfw_score=nsfw_score,
        nsfw_labels=nsfw_labels,
        has_face=has_face,
        face_count=face_count,
        multiple_faces=multiple_faces,
        age_estimate=age_estimate,
        under_18_risk=under_18_risk,
        detected_gender=detected_gender,
        gender_confidence=gender_confidence,
        anchor_match_pct=anchor_match_pct,
        anchor_checked=anchor_checked,
    )
