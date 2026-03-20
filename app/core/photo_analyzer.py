"""
Photo analysis pipeline — 4 layers (all hard rejects):
  1. Quality check   (Pillow / NumPy)   — blur/brightness
  2. Watermark check (EasyOCR)          — stock/copyright keywords only
  3. NSFW check      (NudeNet)          — explicit/nude content
  4. Face check      (DeepFace)         — must contain a visible human face; under-18 rejected

A photo is uploaded only if it passes all 4 layers.
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

BLUR_THRESHOLD       = 30     # Laplacian variance below this → blurry (relaxed)
BRIGHTNESS_MIN       = 20     # Mean pixel below this → too dark
BRIGHTNESS_MAX       = 250    # Mean pixel above this → overexposed
NSFW_CONFIDENCE      = 0.40   # NudeNet confidence threshold for explicit (exposed) labels
NSFW_LINGERIE_CONF   = 0.55   # Threshold for lingerie combination check
MIN_AGE_ALLOWED      = 18     # DeepFace age estimate below this → rejected

# Image corner region fraction (top-left, top-right, bottom-left, bottom-right)
CORNER_FRACTION = 0.25

# Hard reject — any exposure of intimate body parts
NSFW_EXPLICIT_LABELS = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",       # nipple / bare breast
    "ANUS_EXPOSED",
    "MALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",            # bare buttocks / thong
}

# Lingerie rule: reject only when BOTH top AND bottom lingerie are detected together.
# A dress with cleavage alone (FEMALE_BREAST_COVERED) is fine.
# Panties alone from a wide shot may be fine.
# But bra + panties together = underwear/lingerie shoot = reject.
NSFW_LINGERIE_TOP    = "FEMALE_BREAST_COVERED"      # bra / bikini top
NSFW_LINGERIE_BOTTOM = "FEMALE_GENITALIA_COVERED"   # panties / bikini bottom

# Only well-known stock/copyright watermarks trigger rejection (not generic text)
WATERMARK_KEYWORDS = {
    "shutterstock", "getty", "istock", "dreamstime", "alamy",
    "depositphotos", "123rf", "adobe stock", "©", "copyright",
    "watermark",
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
    Only rejects photos that contain known stock/copyright watermark keywords.
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
                if text:
                    all_text_parts.append(text)

        combined = " ".join(all_text_parts).lower()

        # Only reject on known stock/copyright watermark keywords
        for kw in WATERMARK_KEYWORDS:
            if kw in combined:
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

        # Hard reject: explicit exposed content
        explicit_hits = [
            d for d in detections
            if d.get("class") in NSFW_EXPLICIT_LABELS
            and d.get("score", 0) >= NSFW_CONFIDENCE
        ]

        # Combination rule: bra/bikini-top AND panties/bikini-bottom both detected = lingerie
        # A dress showing cleavage alone is fine; panties alone in a wide shot may be fine.
        # Only reject when BOTH are present at the same time (underwear/lingerie outfit).
        top_score    = max((d["score"] for d in detections if d.get("class") == NSFW_LINGERIE_TOP), default=0)
        bottom_score = max((d["score"] for d in detections if d.get("class") == NSFW_LINGERIE_BOTTOM), default=0)
        lingerie_hit = top_score >= NSFW_LINGERIE_CONF and bottom_score >= NSFW_LINGERIE_CONF

        if explicit_hits or lingerie_hit:
            all_hits  = explicit_hits + ([{"class": "LINGERIE_SET", "score": max(top_score, bottom_score)}] if lingerie_hit else [])
            max_score = max(d["score"] for d in all_hits)
            labels    = [d["class"] for d in all_hits]
            return True, round(max_score, 4), labels

        all_scored = [d["score"] for d in detections if d.get("class") in NSFW_EXPLICIT_LABELS]
        max_score  = round(max(all_scored), 4) if all_scored else 0.0
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

        # Try each backend — move to next if current finds no face OR confidence is too low
        DETECTOR_CHAIN = ["retinaface", "opencv", "mtcnn"]
        CONF_THRESHOLD = 0.55  # accept face if any backend reaches this confidence

        best_face_results = None
        best_backend      = None

        for backend in DETECTOR_CHAIN:
            try:
                raw = DeepFace.analyze(
                    img_path=img_array,
                    actions=["age"],
                    detector_backend=backend,
                    enforce_detection=True,
                    silent=True,
                )
                if isinstance(raw, dict):
                    raw = [raw]
                hits = [r for r in raw if r.get("face_confidence", 0.0) >= CONF_THRESHOLD]
                if hits:
                    best_face_results = hits
                    best_backend      = backend
                    break  # good enough — stop trying further backends
                else:
                    logger.info("Face check: backend=%s returned low confidence — trying next", backend)
            except Exception as exc:
                logger.info("Face check: backend=%s no face detected — %s", backend, str(exc)[:80])

        if not best_face_results:
            logger.info("Face check: all backends failed or low confidence — rejecting")
            return False, 0, None, False

        face_count = len(best_face_results)
        ages       = [int(r["age"]) for r in best_face_results]
        avg_age    = int(sum(ages) / face_count) if ages else None
        min_age    = min(ages) if ages else None
        under_18   = min_age is not None and min_age < MIN_AGE_ALLOWED
        logger.info("Face check | backend=%s found=%d ages=%s under18=%s conf=%s",
                    best_backend, face_count, ages, under_18,
                    [round(r.get("face_confidence", 0), 2) for r in best_face_results])
        return True, face_count, avg_age, under_18

    except Exception as exc:
        logger.warning("Face check pipeline failed: %s", exc, exc_info=True)
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

    # ── Verdict — all 4 checks are hard rejects ───────────────────────────────
    rejection_reason: Optional[str] = None

    if is_blurry:
        rejection_reason = (
            f"Photo is too blurry (score: {blur_score:.0f}). "
            "Please upload a clear, sharp photo."
        )
    elif has_watermark:
        rejection_reason = (
            "Photo appears to contain a stock photo watermark. "
            "Please upload an original photo."
        )
    elif nsfw:
        rejection_reason = "Photo contains explicit or adult content and cannot be uploaded."
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
