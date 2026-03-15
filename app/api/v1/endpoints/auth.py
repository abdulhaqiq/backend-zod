"""
Authentication endpoints:
  POST /auth/phone/send-otp    — send OTP via SMS or WhatsApp (Twilio)
  POST /auth/phone/verify-otp  — verify OTP, return token pair
  POST /auth/phone/resend-otp  — resend OTP (same rate limits as send)
  POST /auth/apple             — Apple Sign In
  POST /auth/facebook          — Facebook Sign In
  POST /auth/refresh           — refresh access token
  POST /auth/logout            — revoke refresh token
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_otp,
    hash_refresh_token,
    verify_otp,
)
from app.db.session import get_db
from app.models.otp import OtpCode
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    AppleAuthRequest,
    DeviceInfo,
    FacebookAuthRequest,
    OtpSentResponse,
    PhoneSendOtpRequest,
    PhoneVerifyOtpRequest,
    RefreshRequest,
    TokenResponse,
)
from app.services.apple_auth import verify_apple_token
from app.services.facebook_auth import verify_facebook_token
from app.services.twilio_service import send_otp as twilio_send_otp

import random
import string

router = APIRouter(prefix="/auth", tags=["auth"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

DEV_PHONE_OTPS: dict[str, str] = {
    "+919148880196": "51435",
    "919148880196":  "51435",
    "9148880196":    "51435",
}

def _generate_otp_code(phone: str = "") -> str:
    if phone in DEV_PHONE_OTPS:
        return DEV_PHONE_OTPS[phone]
    return "".join(random.choices(string.digits, k=5))


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _issue_token_pair(
    user_id: str,
    db: AsyncSession,
    device: "DeviceInfo | None" = None,
) -> TokenResponse:
    """Create a new access + refresh token pair and persist the refresh token hash."""
    access_token = create_access_token(subject=user_id)

    raw_refresh = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh)
    now = _now()
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db.add(RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        device_name=device.device_model if device else None,
        device_os=device.device_os if device else None,
        ip_address=device.ip_address if device else None,
        last_used_at=now,
    ))
    await db.flush()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def _get_or_create_user_by_phone(phone: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if not user:
        user = User(phone=phone, is_verified=True)
        db.add(user)
        await db.flush()
    elif not user.is_verified:
        user.is_verified = True
    return user


# ─────────────────────────────────────────────────────────────────────────────
# OTP helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _send_otp_to_phone(
    phone: str,
    channel: str,
    db: AsyncSession,
    device: DeviceInfo | None = None,
) -> OtpSentResponse:
    """
    Core send-OTP logic shared by send and resend endpoints.
    Enforces:
    - Max OTP_MAX_SENDS_PER_HOUR sends per phone per hour
    - Generates fresh OTP, hashes it, stores in DB with device fingerprint
    - Sends via Twilio
    """
    now = _now()
    one_hour_ago = now - timedelta(hours=1)

    # Count sends in the last hour for this phone
    result = await db.execute(
        select(OtpCode).where(
            OtpCode.phone == phone,
            OtpCode.created_at >= one_hour_ago,
        )
    )
    recent_otps = result.scalars().all()
    total_sends_this_hour = sum(o.send_count for o in recent_otps)

    if total_sends_this_hour >= settings.OTP_MAX_SENDS_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many OTP requests. Try again after an hour.",
        )

    # Generate & hash a fresh code
    code = _generate_otp_code(phone)
    code_hash = hash_otp(code)
    expires_at = now + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

    otp_record = OtpCode(
        phone=phone,
        code_hash=code_hash,
        channel=channel,
        expires_at=expires_at,
        ip_address=device.ip_address if device else None,
        device_id=device.device_id if device else None,
        device_model=device.device_model if device else None,
        device_os=device.device_os if device else None,
        network_type=device.network_type if device else None,
    )
    db.add(otp_record)
    await db.flush()

    # Send via Twilio (runs in thread pool, non-blocking)
    try:
        await twilio_send_otp(phone=phone, code=code, channel=channel)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send OTP: {exc}",
        )

    return OtpSentResponse(
        message=f"OTP sent via {channel}",
        channel=channel,
        expires_in_seconds=settings.OTP_EXPIRE_MINUTES * 60,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/phone/send-otp",
    response_model=OtpSentResponse,
    summary="Send OTP via SMS or WhatsApp",
)
async def send_otp(payload: PhoneSendOtpRequest, db: AsyncSession = Depends(get_db)):
    return await _send_otp_to_phone(
        phone=payload.phone, channel=payload.channel, db=db, device=payload.device
    )


@router.post(
    "/phone/resend-otp",
    response_model=OtpSentResponse,
    summary="Resend OTP (same rate limits as send)",
)
async def resend_otp(payload: PhoneSendOtpRequest, db: AsyncSession = Depends(get_db)):
    return await _send_otp_to_phone(
        phone=payload.phone, channel=payload.channel, db=db, device=payload.device
    )


@router.post(
    "/phone/verify-otp",
    response_model=TokenResponse,
    summary="Verify OTP and receive token pair",
)
async def verify_otp_endpoint(
    payload: PhoneVerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    now = _now()

    # Find the most recent unverified, unexpired OTP for this phone
    result = await db.execute(
        select(OtpCode).where(
            OtpCode.phone == payload.phone,
            OtpCode.verified_at.is_(None),
            OtpCode.expires_at > now,
        ).order_by(OtpCode.created_at.desc())
    )
    otp = result.scalars().first()

    if otp is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active OTP found. Please request a new one.",
        )

    # Check if phone is blocked
    if otp.blocked_until and otp.blocked_until > now:
        remaining = int((otp.blocked_until - now).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {remaining} minutes.",
        )

    # Verify the code
    if not verify_otp(payload.code, otp.code_hash):
        otp.attempt_count += 1

        if otp.attempt_count >= settings.OTP_MAX_ATTEMPTS:
            otp.blocked_until = now + timedelta(minutes=settings.OTP_BLOCK_MINUTES)

        # Commit attempt count before raising so rollback doesn't lose it
        await db.commit()

        if otp.attempt_count >= settings.OTP_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed attempts. Phone blocked for {settings.OTP_BLOCK_MINUTES} minutes.",
            )

        remaining_attempts = settings.OTP_MAX_ATTEMPTS - otp.attempt_count
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid OTP code. {remaining_attempts} attempt(s) remaining.",
        )

    # Mark as verified immediately so the code cannot be reused even if later steps fail
    otp.verified_at = now
    # Update device info from the verification request (may differ from send — e.g. different network)
    if payload.device:
        d = payload.device
        if d.ip_address:
            otp.ip_address = d.ip_address
        if d.device_id:
            otp.device_id = d.device_id
        if d.device_model:
            otp.device_model = d.device_model
        if d.device_os:
            otp.device_os = d.device_os
        if d.network_type:
            otp.network_type = d.network_type
    await db.commit()

    # Upsert user
    user = await _get_or_create_user_by_phone(phone=payload.phone, db=db)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive.",
        )

    token_pair = await _issue_token_pair(user_id=str(user.id), db=db, device=payload.device)
    return token_pair


@router.post(
    "/apple",
    response_model=TokenResponse,
    summary="Sign in with Apple",
)
async def apple_sign_in(payload: AppleAuthRequest, db: AsyncSession = Depends(get_db)):
    try:
        apple_data = await verify_apple_token(payload.identity_token)
    except ValueError as exc:
        import logging as _logging
        _logging.getLogger(__name__).error("Apple sign-in rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    apple_id = apple_data["apple_id"]
    email = apple_data.get("email")

    # Look up existing user by apple_id first, then by email
    result = await db.execute(select(User).where(User.apple_id == apple_id))
    user = result.scalar_one_or_none()

    if user is None and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.apple_id = apple_id

    if user is None:
        user = User(
            apple_id=apple_id,
            email=email,
            full_name=payload.full_name,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

    return await _issue_token_pair(user_id=str(user.id), db=db)


@router.post(
    "/facebook",
    response_model=TokenResponse,
    summary="Sign in with Facebook",
)
async def facebook_sign_in(payload: FacebookAuthRequest, db: AsyncSession = Depends(get_db)):
    try:
        fb_data = await verify_facebook_token(payload.access_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    facebook_id = fb_data["facebook_id"]
    email = fb_data.get("email")

    # Look up existing user by facebook_id first, then by email
    result = await db.execute(select(User).where(User.facebook_id == facebook_id))
    user = result.scalar_one_or_none()

    if user is None and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.facebook_id = facebook_id

    if user is None:
        user = User(
            facebook_id=facebook_id,
            email=email,
            full_name=fb_data.get("name"),
            is_verified=True,
        )
        db.add(user)
        await db.flush()

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

    return await _issue_token_pair(user_id=str(user.id), db=db)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using a refresh token",
)
async def refresh_token(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(payload.refresh_token)
    now = _now()

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    if stored.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    if stored.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please log in again.",
        )

    # Rotate: revoke old token, issue new pair — carry device info forward
    stored.revoked_at = now
    await db.flush()

    carried_device = DeviceInfo(
        device_model=stored.device_name,
        device_os=stored.device_os,
        ip_address=stored.ip_address,
    ) if (stored.device_name or stored.ip_address) else None

    return await _issue_token_pair(user_id=str(stored.user_id), db=db, device=carried_device)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke refresh token (logout)",
)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(payload.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
    )
    stored = result.scalar_one_or_none()

    if stored:
        stored.revoked_at = _now()


# ─────────────────────────────────────────────────────────────────────────────
# Session management
# ─────────────────────────────────────────────────────────────────────────────

from app.core.deps import get_current_user  # noqa: E402 – imported here to avoid circular

MAX_SESSIONS = 2


@router.get(
    "/sessions",
    summary="List active sessions for the current user",
)
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = _now()
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        ).order_by(RefreshToken.created_at.desc())
    )
    sessions = result.scalars().all()

    return {
        "sessions": [
            {
                "id": str(s.id),
                "device_name": s.device_name or "Unknown Device",
                "device_os": s.device_os or "",
                "ip_address": s.ip_address or "",
                "created_at": s.created_at.isoformat(),
                "last_used_at": s.last_used_at.isoformat() if s.last_used_at else s.created_at.isoformat(),
                "expires_at": s.expires_at.isoformat(),
            }
            for s in sessions
        ],
        "total": len(sessions),
        "limit": MAX_SESSIONS,
    }


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a specific session by ID",
)
async def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import uuid as _uuid
    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.id == sid,
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Session not found.")
    token.revoked_at = _now()


@router.delete(
    "/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all sessions except the current one is not possible here — revokes all",
)
async def revoke_all_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = _now()
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    tokens = result.scalars().all()
    for t in tokens:
        t.revoked_at = now
