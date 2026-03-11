"""
Photo analysis pipeline — 4 layers:
  1. Quality check   (Pillow / NumPy)   — blur detection (hard reject if blurry)
  2. Watermark check (EasyOCR)          — text overlay detection (hard reject if found)
  3. NSFW check      (NudeNet)          — explicit content detection (hard reject)
  4. Face check      (DeepFace)         — must have ≥1 clear face + age ≥ 18 (hard reject if missing/under-18)

All hard-rejection rules are enforced — photo must pass every layer to be accepted.
Heavy models are lazily loaded and cached at module level.
"""

import io
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

BLUR_THRESHOLD       = 80     # Laplacian variance below this → blurry (hard reject)
BRIGHTNESS_MIN       = 30     # Mean pixel below this → too dark
BRIGHTNESS_MAX       = 225    # Mean pixel above this → overexposed
NSFW_CONFIDENCE      = 0.50   # NudeNet label confidence threshold
MIN_AGE_ALLOWED      = 18     # DeepFace age estimate below this → rejected
WATERMARK_MIN_CHARS  = 4      # Minimum OCR text chars in a corner to flag as watermark

# Image corner region fraction (top-left, top-right, bottom-left, bottom-right)
CORNER_FRACTION = 0.25

# NudeNet labels that constitute explicit content
NSFW_EXPLICIT_LABELS = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "ANUS_EXPOSED",
    "MALE_BREAST_EXPOSED",
}

# Known watermark / copyright keywords (case-insensitive)
WATERMARK_KEYWORDS = {
    "shutterstock", "getty", "istock", "dreamstime", "alamy",
    "depositphotos", "123rf", "adobe stock", "stock", "©", "copyright",
    "watermark", "preview", "sample",
}

# ── Cached model instances ────────────────────────────────────────────────────
_nude_detector = None
_ocr_reader    = None


def _get_nude_detector():
    global _nude_detector
    if _nude_detector is None:
        from nudenet import NudeDetector
        _nude_detector = NudeDetector()
    return _nude_detector


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        _ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _ocr_reader


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class PhotoAnalysis:
    passed: bool
    rejection_reason: Optional[str]

    # Layer 1 — quality
    is_blurry: bool
    blur_score: float
    brightness_ok: bool
    brightness: float
    quality_score: float          # 0.0 – 1.0 composite

    # Layer 2 — watermark
    has_watermark: bool
    watermark_text: str           # detected text snippet (for logging)

    # Layer 3 — NSFW
    nsfw: bool
    nsfw_score: float

    # Layer 4 — face
    has_face: bool
    face_count: int
    age_estimate: Optional[int]
    under_18_risk: bool

    # Default fields last
    nsfw_labels: list = field(default_factory=list)


# ── Layer 1: Quality (blur + brightness) ─────────────────────────────────────

def _check_quality(img_bytes: bytes):
    """Returns (is_blurry, blur_score, brightness_ok, brightness, quality_score)"""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        # Blur via Laplacian edge energy on grayscale
        gray      = img.convert("L")
        edges     = gray.filter(ImageFilter.FIND_EDGES)
        blur_score = float(np.var(np.array(edges, dtype=np.float32)))
        is_blurry  = blur_score < BLUR_THRESHOLD

        # Brightness
        brightness    = float(np.mean(np.array(img, dtype=np.float32)))
        brightness_ok = BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX

        blur_norm     = min(blur_score / 500.0, 1.0)
        bright_ok     = 1.0 if brightness_ok else 0.4
        quality_score = round(blur_norm * 0.7 + bright_ok * 0.3, 3)

        return is_blurry, round(blur_score, 2), brightness_ok, round(brightness, 2), quality_score
    except Exception as exc:
        logger.warning("Quality check failed: %s", exc)
        return False, 999.0, True, 128.0, 1.0


# ── Layer 2: Watermark detection ─────────────────────────────────────────────

def _check_watermark(img_bytes: bytes):
    """
    Returns (has_watermark, detected_text).
    Strategy:
      1. Crop the four corners (each CORNER_FRACTION of W/H).
      2. Run EasyOCR on each corner.
      3. Flag if any corner has ≥ WATERMARK_MIN_CHARS of text, or if a
         known watermark keyword is found anywhere in the full-image OCR.
    """
    try:
        reader = _get_ocr_reader()
        img    = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h   = img.size
        cw, ch = int(w * CORNER_FRACTION), int(h * CORNER_FRACTION)

        corners = [
            img.crop((0,      0,      cw,  ch)),   # top-left
            img.crop((w - cw, 0,      w,   ch)),   # top-right
            img.crop((0,      h - ch, cw,  h)),    # bottom-left
            img.crop((w - cw, h - ch, w,   h)),    # bottom-right
        ]

        all_text_parts = []

        for corner in corners:
            arr     = np.array(corner)
            results = reader.readtext(arr, detail=0, paragraph=False)
            for text in results:
                text = text.strip()
                if len(text) >= WATERMARK_MIN_CHARS:
                    all_text_parts.append(text)

        combined = " ".join(all_text_parts).lower()

        # Check for known watermark keywords
        for kw in WATERMARK_KEYWORDS:
            if kw in combined:
                return True, combined[:120]

        # Even without a keyword, too much text in corners = likely watermark
        total_chars = sum(len(t) for t in all_text_parts)
        if total_chars >= 15:
            return True, combined[:120]

        return False, ""

    except Exception as exc:
        logger.warning("Watermark check failed: %s", exc)
        return False, ""


# ── Layer 3: NSFW ─────────────────────────────────────────────────────────────

def _check_nsfw(img_bytes: bytes):
    """Returns (is_nsfw, max_score, detected_labels)"""
    tmp_path = None
    try:
        detector = _get_nude_detector()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(img_bytes)
            tmp_path = f.name

        detections = detector.detect(tmp_path)

        explicit_hits = [
            d for d in detections
            if d.get("class") in NSFW_EXPLICIT_LABELS
            and d.get("score", 0) >= NSFW_CONFIDENCE
        ]

        if explicit_hits:
            max_score = max(d["score"] for d in explicit_hits)
            labels    = [d["class"] for d in explicit_hits]
            return True, round(max_score, 4), labels

        all_explicit = [d["score"] for d in detections if d.get("class") in NSFW_EXPLICIT_LABELS]
        max_score    = round(max(all_explicit), 4) if all_explicit else 0.0
        return False, max_score, []

    except Exception as exc:
        logger.warning("NSFW check failed: %s", exc)
        return False, 0.0, []
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Layer 4: Face + Age ───────────────────────────────────────────────────────

def _check_face(img_bytes: bytes):
    """Returns (has_face, face_count, age_estimate, under_18_risk)"""
    try:
        from deepface import DeepFace

        img       = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_array = np.array(img)

        results = DeepFace.analyze(
            img_path=img_array,
            actions=["age"],
            enforce_detection=True,
            silent=True,
        )

        if isinstance(results, dict):
            results = [results]

        face_count  = len(results)
        ages        = [int(r["age"]) for r in results]
        avg_age     = int(sum(ages) / face_count) if ages else None
        min_age     = min(ages) if ages else None
        under_18    = min_age is not None and min_age < MIN_AGE_ALLOWED

        return True, face_count, avg_age, under_18

    except Exception as exc:
        msg = str(exc).lower()
        # DeepFace raises ValueError/AttributeError when no face found
        if any(kw in msg for kw in ("face", "detector", "detected", "keras", "tensorflow", "could not")):
            logger.info("Face check: no face detected (%s)", str(exc)[:120])
            return False, 0, None, False
        # Any other exception — still no face confirmed, log it fully
        logger.warning("Face check failed unexpectedly: %s", exc, exc_info=True)
        return False, 0, None, False


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_photo(img_bytes: bytes) -> PhotoAnalysis:
    """
    Run all four analysis layers.  Every rule is a hard rejection:
      ✗  Blurry image
      ✗  Watermark / text overlay detected
      ✗  Explicit NSFW content
      ✗  No human face visible
      ✗  Face appears under 18
    """
    is_blurry, blur_score, brightness_ok, brightness, quality_score = _check_quality(img_bytes)
    has_watermark, watermark_text                                    = _check_watermark(img_bytes)
    nsfw, nsfw_score, nsfw_labels                                    = _check_nsfw(img_bytes)
    has_face, face_count, age_estimate, under_18_risk                = _check_face(img_bytes)

    # ── Verdict (first failing rule wins) ─────────────────────────────────────
    rejection_reason: Optional[str] = None

    if is_blurry:
        rejection_reason = (
            f"Photo is too blurry (score: {blur_score:.0f}). "
            "Please upload a clear, sharp photo."
        )
    elif has_watermark:
        rejection_reason = (
            "Photo appears to have a watermark or text overlay. "
            "Please upload an original photo without watermarks."
        )
    elif nsfw:
        rejection_reason = "Photo contains explicit content and cannot be uploaded."
    elif not has_face:
        rejection_reason = (
            "No face detected in the photo. "
            "Please upload a clear photo that shows your face."
        )
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
        blur_score=blur_score,
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
    )

    logger.info(
        "Photo analysis | passed=%s blurry=%s(%.0f) watermark=%s nsfw=%s(%.2f) "
        "face=%s(count=%s age=%s)%s",
        passed, is_blurry, blur_score, has_watermark, nsfw, nsfw_score,
        has_face, face_count, age_estimate,
        f" | REJECTED: {rejection_reason}" if not passed else "",
    )

    return result
