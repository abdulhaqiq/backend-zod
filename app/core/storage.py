"""
DigitalOcean Spaces (S3-compatible) upload utility.
"""
import uuid
import mimetypes
import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings


def _get_client():
    return boto3.client(
        "s3",
        region_name=settings.DO_SPACES_REGION,
        endpoint_url=settings.DO_SPACES_ENDPOINT,
        aws_access_key_id=settings.DO_SPACES_KEY,
        aws_secret_access_key=settings.DO_SPACES_SECRET,
    )


def upload_file(file_bytes: bytes, content_type: str, folder: str, ext: str) -> str:
    """Generic upload — returns public CDN URL."""
    key = f"{folder}/{uuid.uuid4().hex}{ext}"
    try:
        client = _get_client()
        client.put_object(
            Bucket=settings.DO_SPACES_BUCKET,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
            ACL="public-read",
        )
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Upload failed: {exc}") from exc
    return f"{settings.DO_SPACES_CDN_BASE}/{key}"


def upload_photo(file_bytes: bytes, content_type: str, folder: str = "photos") -> str:
    """
    Upload raw bytes to DO Spaces and return the public CDN URL.
    Raises RuntimeError on failure.
    """
    ext = mimetypes.guess_extension(content_type) or ".jpg"
    # Some systems return '.jpe' for JPEG — normalise it
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"

    key = f"{folder}/{uuid.uuid4().hex}{ext}"

    try:
        client = _get_client()
        client.put_object(
            Bucket=settings.DO_SPACES_BUCKET,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
            ACL="public-read",
        )
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Upload failed: {exc}") from exc

    cdn_url = f"{settings.DO_SPACES_CDN_BASE}/{key}"
    return cdn_url
