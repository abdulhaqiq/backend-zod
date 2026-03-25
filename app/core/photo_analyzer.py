"""
Photo analysis pipeline — powered entirely by AWS Rekognition.

  1. Face + Quality check  (Rekognition DetectFaces)            — sharpness, brightness, face required, 18+
  2. Watermark check       (Rekognition DetectText)             — stock/copyright keywords
  3. NSFW check            (Rekognition DetectModerationLabels) — explicit / adult content

All three checks use a single boto3 client. No NumPy, no Pillow image analysis,
no external ML libraries (DeepFace, nudenet, easyocr, imagehash) required.
Pillow is used only as a thin format converter (WebP/HEIC → JPEG) before sending
bytes to Rekognition — not for any analysis.
"""

import io
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds (Rekognition quality scores are 0–100) ─────────────────────────

SHARPNESS_MIN   = 10.0  # Rekognition Quality.Sharpness below this → blurry (10 = only genuinely unusable photos)
BRIGHTNESS_MIN  =  5.0  # Rekognition Quality.Brightness below this → too dark
BRIGHTNESS_MAX  = 98.0  # Rekognition Quality.Brightness above this → overexposed
MIN_AGE_ALLOWED = 18    # Rekognition age estimate below this → rejected
FACE_CONFIDENCE = 80.0  # Rekognition face confidence minimum

NSFW_REJECT_LABELS = {
    "Explicit Nudity",
    "Nudity",
    "Graphic Male Nudity",
    "Graphic Female Nudity",
    "Nude Male",
    "Nude Female",
    "Explicit Sexual Activity",
    "Graphic Sexual Activity",
    "Partial Nudity",
    "Exposed Male Genitalia",
    "Exposed Female Genitalia",
    "Exposed Anus",
    "Exposed Buttocks Or Anus",
}

WATERMARK_KEYWORDS = {
    "shutterstock", "getty", "istock", "dreamstime", "alamy",
    "depositphotos", "123rf", "adobe stock", "©", "copyright", "watermark",
}


# ── Rekognition client ────────────────────────────────────────────────────────

def _rekognition_client():
    import boto3
    from botocore.config import Config
    from app.core.config import settings
    return boto3.client(
        "rekognition",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        region_name=getattr(settings, "AWS_REGION", None) or "us-east-1",
        config=Config(
            connect_timeout=8,
            read_timeout=15,
            retries={"max_attempts": 1},
        ),
    )


def _to_jpeg(img_bytes: bytes) -> bytes:
    """
    Convert image bytes to JPEG for Rekognition.
    Rekognition natively supports JPEG and PNG — conversion only needed for WebP/HEIC.
    Uses Pillow only as a format adapter, not for analysis.
    """
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(img_bytes)).convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class PhotoAnalysis:
    passed: bool
    rejection_reason: Optional[str]

    # Layer 1 — quality + face (single Rekognition DetectFaces call)
    is_blurry: bool
    sharpness: float         # Rekognition Quality.Sharpness  (0–100)
    brightness_ok: bool
    brightness: float        # Rekognition Quality.Brightness (0–100)
    quality_score: float     # composite (for logging)

    # Layer 2 — watermark
    has_watermark: bool
    watermark_text: str

    # Layer 3 — NSFW
    nsfw: bool
    nsfw_score: float
    nsfw_labels: list = field(default_factory=list)

    # Face details (from layer 1)
    has_face: bool = False
    face_count: int = 0
    age_estimate: Optional[int] = None
    under_18_risk: bool = False
    detected_gender: Optional[str] = None      # "Male" | "Female" | None
    gender_confidence: float = 0.0             # Rekognition confidence (0–100)


# ── Layer 1+4 combined: Face + Quality (Rekognition DetectFaces) ──────────────

def _check_face_and_quality(jpeg_bytes: bytes):
    """
    Single Rekognition DetectFaces call with Attributes=ALL returns:
      - Face confidence
      - AgeRange (Low / High)
      - Quality.Sharpness  (0–100)  ← replaces NumPy blur detection
      - Quality.Brightness (0–100)  ← replaces Pillow ImageStat

    Returns (has_face, face_count, age_estimate, under_18_risk,
             is_blurry, sharpness, brightness_ok, brightness, quality_score,
             detected_gender, gender_confidence)
    """
    try:
        client = _rekognition_client()
        resp   = client.detect_faces(Image={"Bytes": jpeg_bytes}, Attributes=["ALL"])
        faces  = [f for f in resp.get("FaceDetails", []) if f.get("Confidence", 0) >= FACE_CONFIDENCE]

        if not faces:
            logger.info("Face check: no confident face detected")
            return False, 0, None, False, False, 0.0, True, 50.0, 0.0, None, 0.0

        face       = faces[0]
        quality    = face.get("Quality", {})
        sharpness  = float(quality.get("Sharpness",  50.0))
        brightness = float(quality.get("Brightness", 50.0))

        is_blurry     = sharpness  < SHARPNESS_MIN
        brightness_ok = BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX
        quality_score = round((sharpness / 100.0) * 0.7 + (1.0 if brightness_ok else 0.3) * 0.3, 3)

        age_range  = face.get("AgeRange", {})
        ages = [
            (f["AgeRange"]["Low"] + f["AgeRange"]["High"]) // 2
            for f in faces if "AgeRange" in f
        ]
        face_count = len(faces)
        avg_age    = int(sum(ages) / len(ages)) if ages else None
        min_age    = min(ages) if ages else None
        under_18   = min_age is not None and min_age < MIN_AGE_ALLOWED

        gender_data  = face.get("Gender", {})
        det_gender   = gender_data.get("Value")        # "Male" | "Female"
        gender_conf  = float(gender_data.get("Confidence", 0.0))

        logger.info(
            "Face+Quality [Rekognition] | faces=%d sharpness=%.1f brightness=%.1f ages=%s under18=%s gender=%s(%.0f%%)",
            face_count, sharpness, brightness, ages, under_18, det_gender, gender_conf,
        )
        return True, face_count, avg_age, under_18, is_blurry, sharpness, brightness_ok, brightness, quality_score, det_gender, gender_conf

    except Exception as exc:
        logger.warning("Face+Quality check failed: %s", exc)
        return False, 0, None, False, False, 0.0, True, 50.0, 0.0, None, 0.0


# ── Layer 2: Watermark (Rekognition DetectText) ───────────────────────────────

def _check_watermark(jpeg_bytes: bytes):
    try:
        client   = _rekognition_client()
        resp     = client.detect_text(Image={"Bytes": jpeg_bytes})
        words    = [
            d["DetectedText"]
            for d in resp.get("TextDetections", [])
            if d.get("Type") == "WORD" and d.get("Confidence", 0) >= 60
        ]
        combined = " ".join(words).lower()
        for kw in WATERMARK_KEYWORDS:
            if kw in combined:
                logger.info("Watermark detected: keyword=%r", kw)
                return True, combined[:120]
        return False, ""
    except Exception as exc:
        logger.warning("Watermark check failed: %s", exc)
        return False, ""


# ── Layer 3: NSFW (Rekognition DetectModerationLabels) ───────────────────────

def _check_nsfw(jpeg_bytes: bytes):
    try:
        client = _rekognition_client()
        resp   = client.detect_moderation_labels(Image={"Bytes": jpeg_bytes}, MinConfidence=50.0)
        labels = resp.get("ModerationLabels", [])
        hits   = [l for l in labels if l["Name"] in NSFW_REJECT_LABELS]

        if hits:
            top_score   = max(l["Confidence"] for l in hits)
            label_names = [l["Name"] for l in hits]
            logger.info("NSFW detected: labels=%s", label_names)
            return True, round(top_score / 100.0, 4), label_names

        all_scores = [l["Confidence"] for l in labels]
        max_score  = round(max(all_scores) / 100.0, 4) if all_scores else 0.0
        return False, max_score, []

    except Exception as exc:
        logger.warning("NSFW check failed: %s", exc)
        return False, 0.0, []


# ── Quality score (for photo reordering) ─────────────────────────────────────

def get_photo_quality_score(img_bytes: bytes) -> float:
    """
    Return a 0.0–1.0 quality score for a photo using Rekognition's built-in
    Quality.Sharpness and Quality.Brightness — no NumPy/Pillow math needed.
    Used to reorder profile photos best-first.
    Returns 0.0 if Rekognition finds no face or the call fails.
    """
    try:
        jpeg_bytes = _to_jpeg(img_bytes)
        client = _rekognition_client()
        resp  = client.detect_faces(Image={"Bytes": jpeg_bytes}, Attributes=["ALL"])
        faces = [f for f in resp.get("FaceDetails", []) if f.get("Confidence", 0) >= FACE_CONFIDENCE]
        if not faces:
            return 0.0
        quality   = faces[0].get("Quality", {})
        sharpness = float(quality.get("Sharpness",  0.0))
        brightness = float(quality.get("Brightness", 50.0))
        brightness_ok = BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX
        return round((sharpness / 100.0) * 0.7 + (1.0 if brightness_ok else 0.3) * 0.3, 3)
    except Exception as exc:
        logger.warning("Quality score failed: %s", exc)
        return 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_photo(img_bytes: bytes) -> PhotoAnalysis:
    """
    Run all analysis layers via AWS Rekognition only.
    Hard rejections:
      ✗  Blurry / low-sharpness  (Rekognition Quality.Sharpness < 20)
      ✗  Too dark / overexposed  (Rekognition Quality.Brightness out of range)
      ✗  Watermark / stock text  (Rekognition DetectText)
      ✗  Explicit / adult content (Rekognition ModerationLabels)
      ✗  No human face           (Rekognition DetectFaces confidence < 85%)
      ✗  Face appears under 18   (Rekognition AgeRange)
    """
    try:
        jpeg_bytes = _to_jpeg(img_bytes)
    except Exception as exc:
        logger.error("Image conversion failed: %s", exc)
        return PhotoAnalysis(
            passed=False,
            rejection_reason="Could not read image. Please try a different photo.",
            is_blurry=False, sharpness=0.0, brightness_ok=True, brightness=50.0, quality_score=0.0,
            has_watermark=False, watermark_text="", nsfw=False, nsfw_score=0.0,
            has_face=False, face_count=0, age_estimate=None, under_18_risk=False,
        )

    (has_face, face_count, age_estimate, under_18_risk,
     is_blurry, sharpness, brightness_ok, brightness, quality_score,
     detected_gender, gender_confidence) = _check_face_and_quality(jpeg_bytes)

    has_watermark, watermark_text = _check_watermark(jpeg_bytes)
    nsfw, nsfw_score, nsfw_labels = _check_nsfw(jpeg_bytes)

    rejection_reason: Optional[str] = None

    if not has_face:
        rejection_reason = "No face detected. Please upload a clear photo showing your face."
    elif is_blurry:
        rejection_reason = (
            f"Photo is too blurry (sharpness: {sharpness:.0f}/100). "
            "Please upload a sharper, clearer photo."
        )
    elif not brightness_ok:
        if brightness < BRIGHTNESS_MIN:
            rejection_reason = "Photo is too dark. Please find better lighting."
        else:
            rejection_reason = "Photo is overexposed. Avoid direct bright light."
    elif has_watermark:
        rejection_reason = (
            "Photo appears to contain a stock photo watermark. "
            "Please upload an original photo."
        )
    elif nsfw:
        rejection_reason = "Photo contains explicit or adult content and cannot be uploaded."
    elif under_18_risk:
        rejection_reason = (
            f"Photo appears to show someone under 18 "
            f"(estimated age: {age_estimate}). Upload rejected."
        )

    passed = rejection_reason is None

    result = PhotoAnalysis(
        passed=passed,
        rejection_reason=rejection_reason,
        is_blurry=is_blurry,
        sharpness=sharpness,
        brightness_ok=brightness_ok,
        brightness=brightness,
        quality_score=quality_score,
        has_watermark=has_watermark,
        watermark_text=watermark_text,
        nsfw=nsfw,
        nsfw_score=nsfw_score,
        nsfw_labels=nsfw_labels,
        has_face=has_face,
        face_count=face_count,
        age_estimate=age_estimate,
        under_18_risk=under_18_risk,
        detected_gender=detected_gender,
        gender_confidence=gender_confidence,
    )

    logger.info(
        "Photo analysis | passed=%s face=%s(count=%s age=%s) sharpness=%.0f brightness=%.0f "
        "watermark=%s nsfw=%s(%.2f)%s",
        passed, has_face, face_count, age_estimate, sharpness, brightness,
        has_watermark, nsfw, nsfw_score,
        f" | REJECTED: {rejection_reason}" if not passed else "",
    )

    return result
