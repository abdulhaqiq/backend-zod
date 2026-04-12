"""
AWS Rekognition photo moderation service.

Scans a list of photo URLs for explicit / suggestive content.
Returns True if any photo is flagged (confidence >= 75).

Required env vars:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
"""
import logging

import boto3
import httpx

from app.core.config import settings

_log = logging.getLogger(__name__)


def _rekognition_client():
    return boto3.client(
        "rekognition",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


async def scan_user_photos(photo_urls: list[str]) -> bool:
    """Returns True if any photo contains explicit/suggestive content."""
    if not photo_urls:
        return False

    rek = _rekognition_client()

    async with httpx.AsyncClient(timeout=15) as http:
        for url in photo_urls:
            try:
                resp = await http.get(url)
                resp.raise_for_status()
                result = rek.detect_moderation_labels(
                    Image={"Bytes": resp.content},
                    MinConfidence=75,
                )
                if result.get("ModerationLabels"):
                    _log.warning("Rekognition flagged photo: %s labels=%s", url, result["ModerationLabels"])
                    return True
            except Exception as exc:
                _log.error("Rekognition scan failed for %s: %s", url, exc)

    return False
