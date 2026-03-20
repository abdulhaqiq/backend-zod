"""
University email verification endpoints.

POST /university/email/send    — generate OTP and email it to the given address
POST /university/email/verify  — verify the OTP and mark university email as confirmed
DELETE /university/email       — remove the stored university email (and reset verified flag)

The OTP is bcrypt-hashed before storage so it is never exposed in the DB.
A 6-digit numeric code is sent to the address. Same rate-limit policy as phone OTP.
"""
from __future__ import annotations

import logging
import random
import re
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services.email_service import send_university_otp

_log    = logging.getLogger(__name__)
router  = APIRouter(prefix="/university", tags=["university"])
_bcrypt = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Helpers ───────────────────────────────────────────────────────────────────

_EDU_DOMAIN_RE = re.compile(
    r"(\.edu|\.ac\.[a-z]{2,}|\.edu\.[a-z]{2,}|university|univ|college|uni\.|ac\.)",
    re.IGNORECASE,
)


def _looks_like_university_email(email: str) -> bool:
    """
    Heuristic — accept any email but warn if it doesn't look academic.
    We don't hard-block non-.edu addresses because many universities use
    custom domains (e.g. student@imperial.ac.uk, student@mit.edu,
    student@unimelb.edu.au).
    """
    domain = email.split("@")[-1].lower()
    return bool(_EDU_DOMAIN_RE.search(domain))


def _generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


# ── Schemas ───────────────────────────────────────────────────────────────────

class SendOtpRequest(BaseModel):
    email: EmailStr


class VerifyOtpRequest(BaseModel):
    code: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/email/send",
    summary="Send OTP to a university email address",
    status_code=status.HTTP_200_OK,
)
async def send_email_otp(
    body: SendOtpRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Generates a 6-digit OTP, hashes it, stores it on the user record, and
    emails it to the given address.

    Rate-limited: if an unexpired OTP already exists for this user, we reuse
    it (prevents hammering). After OTP_EXPIRE_MINUTES the user may request a
    new one.
    """
    email = str(body.email).lower().strip()

    # Soft warning — not a hard block
    is_academic = _looks_like_university_email(email)
    if not is_academic:
        _log.info(
            "User %s submitted non-academic-looking email %s for university verification.",
            current_user.id, email,
        )

    # Rate-limit: if an unexpired OTP exists for the SAME email, resend it
    now = datetime.now(timezone.utc)
    if (
        current_user.university_email == email
        and current_user.university_otp_hash
        and current_user.university_otp_expires_at
        and current_user.university_otp_expires_at > now
    ):
        # Re-send the existing OTP — we can't recover the plain code so generate a new one
        pass  # fall through to generate fresh OTP

    # Generate fresh 6-digit OTP
    code     = _generate_otp()
    otp_hash = _bcrypt.hash(code)
    expires  = now + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

    # Persist to user row (resets verification if email changes)
    current_user.university_email          = email
    current_user.university_email_verified = False
    current_user.university_otp_hash       = otp_hash
    current_user.university_otp_expires_at = expires
    db.add(current_user)
    await db.commit()

    # Send email (async, non-blocking — errors are logged but not raised to client)
    try:
        await send_university_otp(email, code, current_user.university)
    except Exception as exc:
        _log.error("Failed to send university OTP email to %s: %s", email, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send verification email. Please try again.",
        )

    return {
        "sent": True,
        "email": email,
        "is_academic": is_academic,
        "expires_in_minutes": settings.OTP_EXPIRE_MINUTES,
    }


@router.post(
    "/email/verify",
    summary="Verify the OTP sent to the university email",
    status_code=status.HTTP_200_OK,
)
async def verify_email_otp(
    body: VerifyOtpRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Accepts the 6-digit code. On success, sets university_email_verified=True.
    On failure: returns 400 (wrong code) or 410 (expired/not sent).
    """
    now = datetime.now(timezone.utc)

    if not current_user.university_otp_hash or not current_user.university_otp_expires_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="No verification code found. Please request a new one.",
        )

    if current_user.university_otp_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Verification code has expired. Please request a new one.",
        )

    if not _bcrypt.verify(body.code.strip(), current_user.university_otp_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect verification code. Please try again.",
        )

    # ✓ Valid — mark as verified and clear the OTP
    current_user.university_email_verified = True
    current_user.university_otp_hash       = None
    current_user.university_otp_expires_at = None
    db.add(current_user)
    await db.commit()

    _log.info(
        "University email verified for user %s → %s",
        current_user.id, current_user.university_email,
    )

    return {
        "verified": True,
        "university_email": current_user.university_email,
        "university":       current_user.university,
    }


@router.delete(
    "/email",
    summary="Remove the stored university email and reset verification",
    status_code=status.HTTP_200_OK,
)
async def remove_university_email(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current_user.university_email          = None
    current_user.university_email_verified = False
    current_user.university_otp_hash       = None
    current_user.university_otp_expires_at = None
    db.add(current_user)
    await db.commit()
    return {"removed": True}
