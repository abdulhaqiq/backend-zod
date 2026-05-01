"""
Authentication endpoints:
  POST /auth/phone/send-otp    — send OTP via SMS or WhatsApp (Twilio)
  POST /auth/phone/verify-otp  — verify OTP, return token pair
  POST /auth/phone/resend-otp  — resend OTP (same rate limits as send)
  POST /auth/phone/link        — link a verified phone to an existing social account
  POST /auth/apple             — Apple Sign In
  POST /auth/google            — Google Sign In
  POST /auth/facebook          — Facebook Sign In
  POST /auth/refresh           — refresh access token
  POST /auth/logout            — revoke refresh token
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.models.login_event import LoginEvent
from app.models.otp import OtpCode
from app.models.refresh_token import RefreshToken
from app.models.user import User

# Maximum number of concurrent active sessions per user.
# Any older sessions beyond this cap are revoked automatically on new login/refresh.
MAX_SESSIONS = 2
from app.schemas.auth import (
    AppleAuthRequest,
    DeviceInfo,
    FacebookAuthRequest,
    GoogleAuthRequest,
    OtpSentResponse,
    PhoneLinkRequest,
    PhoneSendOtpRequest,
    PhoneVerifyOtpRequest,
    RefreshRequest,
    TokenResponse,
)
from app.services.apple_auth import verify_apple_token
from app.services.facebook_auth import verify_facebook_token
from app.services.google_auth import verify_google_token
from app.services.twilio_service import send_otp as twilio_send_otp

import random
import string
import uuid as _uuid_mod

from app.core.limiter import limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/auth", tags=["auth"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Test/internal accounts — bypass all OTP rate limits
_TEST_PHONE_LAST10 = {"5838175920", "9148880196"}

def _is_test_phone(phone: str) -> bool:
    """True when the phone belongs to a whitelisted test account."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits[-10:] in _TEST_PHONE_LAST10


def _otp_rate_key(request: Request) -> str:
    """
    Per-phone rate-limit key (sync — slowapi does not await key_func).
    Reads from the already-cached body (FastAPI populates request._body
    during Pydantic model dependency injection before the handler runs).
    Test phones get a unique UUID so they never hit any limit bucket.
    """
    import json as _json
    try:
        body_bytes: bytes | None = getattr(request, "_body", None)
        if body_bytes:
            phone = _json.loads(body_bytes).get("phone", "")
            if _is_test_phone(phone):
                return f"test-exempt-{_uuid_mod.uuid4()}"
            return f"otp:{phone}" if phone else get_remote_address(request)
    except Exception:
        pass
    return get_remote_address(request)


DEV_PHONE_OTPS: dict[str, str] = {
    "+919148880196": "51435",
    "919148880196":  "51435",
    "9148880196":    "51435",
    "+915838175920": "27790",
    "915838175920":  "27790",
    "5838175920":    "27790",
}

def _generate_otp_code(phone: str = "") -> str:
    if phone in DEV_PHONE_OTPS:
        return DEV_PHONE_OTPS[phone]
    return "".join(random.choices(string.digits, k=5))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_client_ip(request: Request) -> str | None:
    """
    Extract the real client IP from the request, preferring proxy headers.
    Checks X-Forwarded-For, X-Real-IP, CF-Connecting-IP (Cloudflare),
    then falls back to the direct connection address.
    """
    for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        value = request.headers.get(header)
        if value:
            # X-Forwarded-For can be "client, proxy1, proxy2" — take leftmost
            return value.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _check_ban_status(user: "User", incoming_device_id: "str | None" = None) -> None:
    """Raise 403 if the account is banned or the device is blacklisted.
    Soft-deleted accounts are intentionally NOT rejected here — they will be
    reset to a fresh profile by the caller before a token is issued."""
    if user.device_blacklisted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This device has been permanently banned.",
        )
    # Only block accounts that are both banned (trust_score <= 0) AND inactive.
    # Soft-deleted accounts have is_active=False but is_deleted=True, so the
    # caller resets them first — by the time we reach this check is_deleted is
    # already False and is_active is True.
    if user.trust_score is not None and user.trust_score <= 0 and not user.is_active and not user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been banned.",
        )


async def _check_device_already_registered(db: AsyncSession, device: "DeviceInfo | None", current_user_id: "str | None" = None) -> None:
    """
    Check if this device is already registered with another active account.
    Raises 403 if the device is already in use by a different account.
    This prevents users from creating multiple accounts on the same device.
    
    Args:
        db: Database session
        device: Device information from the request
        current_user_id: Optional - ID of the user being created/updated (to exclude from check)
    """
    if not device or not device.device_id:
        return  # No device ID provided, skip check
    
    # Check if another active (non-deleted, non-banned) user already has this device_id
    query = select(User.id, User.phone, User.email).where(
        User.device_id == device.device_id,
        User.is_deleted.is_(False),
        User.is_banned.is_(False),
    )
    
    # If updating an existing user, exclude them from the check
    if current_user_id:
        query = query.where(User.id != current_user_id)
    
    result = await db.execute(query.limit(1))
    existing_user = result.first()
    
    if existing_user:
        # Device is already registered with another account
        # Mask the phone/email for privacy
        identifier = existing_user.phone or existing_user.email
        masked = f"{identifier[:3]}***{identifier[-3:]}" if identifier and len(identifier) > 6 else "****"
        
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This device is already registered with another account ({masked}). Please use your existing account or contact support.",
        )


async def _issue_token_pair(
    user_id: str,
    db: AsyncSession,
    device: "DeviceInfo | None" = None,
    is_new_user: bool = False,
    auth_method: str = "unknown",
    request: "Request | None" = None,
) -> TokenResponse:
    """Create a new access + refresh token pair and persist the refresh token hash."""
    access_token = create_access_token(subject=user_id)

    raw_refresh = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh)
    now = _now()
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    # Server-derived IP (trusted) — always prefer this over client-reported
    server_ip = _get_client_ip(request) if request else None
    user_agent = request.headers.get("user-agent") if request else None

    # Use server IP for storage; fall back to client-reported only when
    # running behind a local dev proxy that strips headers
    resolved_ip = server_ip or (device.ip_address if device else None)

    db.add(RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        device_name=device.device_name or device.device_model if device else None,
        device_os=device.device_os if device else None,
        ip_address=resolved_ip,
        last_used_at=now,
    ))

    # Keep the user's device_id fresh so device-level blocks stay accurate.
    # We only overwrite when a real device_id is present (never blank it out).
    if device and device.device_id:
        result = await db.execute(select(User).where(User.id == user_id))
        user_row = result.scalar_one_or_none()
        if user_row:
            # Reject if any other account sharing this device_id has been blacklisted
            blacklisted_result = await db.execute(
                select(User.id).where(
                    User.device_id == device.device_id,
                    User.device_blacklisted.is_(True),
                ).limit(1)
            )
            if blacklisted_result.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This device has been permanently banned.",
                )
            user_row.device_id = device.device_id

    # Write immutable login audit event
    db.add(LoginEvent(
        user_id=user_id,
        auth_method=auth_method,
        is_new_user=is_new_user,
        success=True,
        ip_address=resolved_ip,
        user_agent=user_agent,
        device_id=device.device_id if device else None,
        device_model=device.device_model if device else None,
        device_os=device.device_os if device else None,
        device_name=device.device_name if device else None,
        app_version=device.app_version if device else None,
        network_type=device.network_type if device else None,
        carrier=device.carrier if device else None,
    ))

    await db.flush()

    # Enforce MAX_SESSIONS: keep only the N most-recent active tokens; revoke the rest.
    # This runs after flush so the new token is already visible in the query.
    _active_q = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        ).order_by(RefreshToken.created_at.desc())
    )
    _active_tokens = _active_q.scalars().all()
    for _old_token in _active_tokens[MAX_SESSIONS:]:
        _old_token.revoked_at = now

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        is_new_user=is_new_user,
    )


async def _get_or_create_user_by_phone(phone: str, db: AsyncSession, device: "DeviceInfo | None" = None) -> tuple["User", bool]:
    """Returns (user, is_new_user) — is_new_user is True when the row was just created."""
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if not user:
        # Check if device is already registered with another account
        await _check_device_already_registered(db, device)
        
        user = User(phone=phone, is_verified=False, face_scan_required=True, filter_max_distance_km=20)
        db.add(user)
        await db.flush()
        return user, True
    elif user.is_deleted:
        # Re-registration after soft-delete — reset all profile data so the
        # user goes through onboarding fresh without a duplicate phone row.
        _reset_deleted_user(user)
        return user, True
    else:
        # Existing user - check if device_id needs to be populated
        if device and device.device_id:
            # If user doesn't have a device_id yet, add it
            if not user.device_id:
                user.device_id = device.device_id
            # If user has a different device_id, check for conflicts
            elif user.device_id != device.device_id:
                # Check if the new device is already registered with another account
                await _check_device_already_registered(db, device, current_user_id=str(user.id))
        
        if not user.is_verified and not user.face_scan_required:
            # Existing user who never completed face verification — send them through it now
            user.face_scan_required = True
        return user, False


def _reset_deleted_user(user: "User") -> None:
    """Wipe all profile/onboarding fields so a soft-deleted user can re-register fresh."""
    from datetime import datetime, timezone as _tz
    user.is_deleted          = False
    user.deleted_at          = None
    user.is_active           = True
    user.is_onboarded        = False
    user.is_verified         = False
    user.face_scan_required  = True
    user.full_name           = None
    user.date_of_birth       = None
    user.gender_id           = None
    user.bio                 = None
    user.purpose             = None
    user.height_cm           = None
    user.interests           = None
    user.lifestyle           = None
    user.values_list         = None
    user.prompts             = None
    user.photos              = None
    user.voice_prompts       = None
    user.causes              = None
    user.work_experience     = None
    user.education           = None
    user.city                = None
    user.hometown            = None
    user.address             = None
    # Halal / work profile
    user.sect_id             = None
    user.prayer_frequency_id = None
    user.marriage_timeline_id = None
    user.wali_email          = None
    user.wali_verified       = False
    user.blur_photos_halal   = False
    user.halal_mode_enabled  = False
    user.work_mode_enabled   = False
    user.work_photos         = None
    user.work_prompts        = None
    user.work_matching_goals = None
    user.work_are_you_hiring = None
    user.work_headline       = None
    user.work_persona        = None
    # Verification
    user.verification_status = "unverified"
    user.face_match_score    = None
    # Note: face_scan_required is already set to True above (line 327)
    # DO NOT reset it to False here - re-registered users must complete verification
    user.id_scan_required    = False
    # Subscription — downgrade to free
    user.subscription_tier       = "free"
    user.subscription_expires_at = None
    user.revenuecat_customer_id  = None
    # Safety — reset trust score; device_blacklisted intentionally kept
    # so a banned device can't exploit delete-and-re-register to bypass bans.
    user.trust_score = 10
    # Mood
    user.mood_emoji = None
    user.mood_text  = None
    # Filters — clear all saved preferences
    user.filter_age_min         = None
    user.filter_age_max         = None
    user.filter_max_distance_km = 20
    user.filter_verified_only   = False
    user.filter_star_signs      = None
    user.filter_interests       = None
    user.filter_languages       = None
    user.filter_religions       = None
    user.filter_ethnicities     = None
    user.filter_looking_for     = None
    user.filter_education_level = None
    user.filter_family_plans    = None
    user.filter_have_kids       = None
    user.filter_sect            = None
    user.filter_prayer_frequency = None
    user.filter_marriage_timeline = None
    user.filter_wali_verified_only = False
    user.filter_wants_to_work   = None


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

    # Count sends in the last hour for this phone (skipped for test accounts)
    if not _is_test_phone(phone):
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
                detail="Too many OTP requests. Try again after an hour.",
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
@limiter.limit("5/minute;20/hour", key_func=_otp_rate_key)
async def send_otp(request: Request, payload: PhoneSendOtpRequest, db: AsyncSession = Depends(get_db)):
    return await _send_otp_to_phone(
        phone=payload.phone, channel=payload.channel, db=db, device=payload.device
    )


@router.post(
    "/phone/resend-otp",
    response_model=OtpSentResponse,
    summary="Resend OTP (same rate limits as send)",
)
@limiter.limit("5/minute;20/hour", key_func=_otp_rate_key)
async def resend_otp(request: Request, payload: PhoneSendOtpRequest, db: AsyncSession = Depends(get_db)):
    return await _send_otp_to_phone(
        phone=payload.phone, channel=payload.channel, db=db, device=payload.device
    )


@router.post(
    "/phone/verify-otp",
    response_model=TokenResponse,
    summary="Verify OTP and receive token pair",
)
@limiter.limit("10/minute")
async def verify_otp_endpoint(
    request: Request,
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

    # Check if phone is blocked (skipped for test accounts)
    if not _is_test_phone(payload.phone) and otp.blocked_until and otp.blocked_until > now:
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

    # Upsert user — do this BEFORE consuming the OTP so a recoverable error
    # (e.g. snoozed account) doesn't permanently burn the code.
    user, is_new_user = await _get_or_create_user_by_phone(phone=payload.phone, db=db, device=payload.device)

    # Reject banned accounts / blacklisted devices before doing anything else.
    _check_ban_status(user, incoming_device_id=payload.device.device_id if payload.device else None)

    # Signing in is an intentional action — if the account was snoozed
    # (is_active=False via the snooze toggle), automatically re-activate it.
    # A hard-banned account would need a separate flag; is_active alone is
    # used only for snooze/discovery visibility.
    if not user.is_active:
        user.is_active = True

    # Mark OTP as verified so the code cannot be reused
    otp.verified_at = now
    # Update device info from the verification request (may differ from send)
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

    token_pair = await _issue_token_pair(
        user_id=str(user.id), db=db, device=payload.device,
        is_new_user=is_new_user, auth_method="phone_otp", request=request,
    )
    return token_pair


# Import here (not at top) to avoid circular import with app.core.deps
from app.core.deps import get_current_user as _get_current_user  # noqa: E402


@router.post(
    "/phone/link",
    summary="Link a verified phone number to the current (social) account",
)
@limiter.limit("10/minute")
async def link_phone_to_account(
    request: Request,
    payload: PhoneLinkRequest,
    current_user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    After a social sign-in (Google / Apple) for a new user, the mobile app
    collects and verifies a phone number then calls this endpoint to attach it
    to the already-authenticated account.  Unlike /phone/verify-otp this
    endpoint does NOT create a new user — it only updates the phone on the
    account that owns the provided Bearer token.
    """
    now = _now()

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

    if not _is_test_phone(payload.phone) and otp.blocked_until and otp.blocked_until > now:
        remaining = int((otp.blocked_until - now).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {remaining} minutes.",
        )

    if not verify_otp(payload.code, otp.code_hash):
        otp.attempt_count += 1
        if otp.attempt_count >= settings.OTP_MAX_ATTEMPTS:
            otp.blocked_until = now + timedelta(minutes=settings.OTP_BLOCK_MINUTES)
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

    # OTP is valid — consume it and attach the phone to this account
    otp.verified_at = now
    current_user.phone = payload.phone
    await db.commit()

    return {"message": "Phone linked successfully"}


@router.post(
    "/apple",
    response_model=TokenResponse,
    summary="Sign in with Apple",
)
@limiter.limit("20/minute")
async def apple_sign_in(request: Request, payload: AppleAuthRequest, db: AsyncSession = Depends(get_db)):
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
        # Check if device is already registered with another account
        await _check_device_already_registered(db, payload.device)
        
        user = User(
            apple_id=apple_id,
            email=email,
            full_name=payload.full_name,
            is_verified=False,
            face_scan_required=True,
        )
        db.add(user)
        await db.flush()
        is_new_user = True
    elif user.is_deleted:
        _reset_deleted_user(user)
        user.apple_id = apple_id
        if email:
            user.email = email
        is_new_user = True
    else:
        # Existing user - check if device_id needs to be populated
        is_new_user = False
        if payload.device and payload.device.device_id:
            # If user doesn't have a device_id yet, add it
            if not user.device_id:
                user.device_id = payload.device.device_id
            # If user has a different device_id, check for conflicts
            elif user.device_id != payload.device.device_id:
                # Check if the new device is already registered with another account
                await _check_device_already_registered(db, payload.device, current_user_id=str(user.id))

    _check_ban_status(user)
    if not user.is_active:
        user.is_active = True  # Re-activate on sign-in (un-snooze)
    await db.flush()

    return await _issue_token_pair(
        user_id=str(user.id), db=db, device=payload.device,
        is_new_user=is_new_user, auth_method="apple", request=request,
    )


@router.post(
    "/facebook",
    response_model=TokenResponse,
    summary="Sign in with Facebook",
)
@limiter.limit("20/minute")
async def facebook_sign_in(request: Request, payload: FacebookAuthRequest, db: AsyncSession = Depends(get_db)):
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
        # Check if device is already registered with another account
        await _check_device_already_registered(db, payload.device)
        
        user = User(
            facebook_id=facebook_id,
            email=email,
            full_name=fb_data.get("name"),
            is_verified=False,
            face_scan_required=True,
        )
        db.add(user)
        await db.flush()
        is_new_user = True
    elif user.is_deleted:
        _reset_deleted_user(user)
        user.facebook_id = facebook_id
        if email:
            user.email = email
        is_new_user = True
    else:
        # Existing user - check if device_id needs to be populated
        is_new_user = False
        if payload.device and payload.device.device_id:
            # If user doesn't have a device_id yet, add it
            if not user.device_id:
                user.device_id = payload.device.device_id
            # If user has a different device_id, check for conflicts
            elif user.device_id != payload.device.device_id:
                # Check if the new device is already registered with another account
                await _check_device_already_registered(db, payload.device, current_user_id=str(user.id))

    _check_ban_status(user)
    if not user.is_active:
        user.is_active = True  # Re-activate on sign-in (un-snooze)
    await db.flush()

    return await _issue_token_pair(
        user_id=str(user.id), db=db, device=payload.device,
        is_new_user=is_new_user, auth_method="facebook", request=request,
    )


@router.post(
    "/google",
    response_model=TokenResponse,
    summary="Sign in with Google",
)
@limiter.limit("20/minute")
async def google_sign_in(request: Request, payload: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    try:
        google_data = await verify_google_token(payload.access_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    google_id = google_data["google_id"]
    email = google_data.get("email")

    # Look up existing user by google_id first, then by email
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if user is None and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.google_id = google_id

    if user is None:
        # Check if device is already registered with another account
        await _check_device_already_registered(db, payload.device)
        
        user = User(
            google_id=google_id,
            email=email,
            full_name=google_data.get("name"),
            is_verified=False,
            face_scan_required=True,
        )
        db.add(user)
        await db.flush()
        is_new_user = True
    elif user.is_deleted:
        _reset_deleted_user(user)
        user.google_id = google_id
        if email:
            user.email = email
        is_new_user = True
    else:
        # Existing user - check if device_id needs to be populated
        is_new_user = False
        if payload.device and payload.device.device_id:
            # If user doesn't have a device_id yet, add it
            if not user.device_id:
                user.device_id = payload.device.device_id
            # If user has a different device_id, check for conflicts
            elif user.device_id != payload.device.device_id:
                # Check if the new device is already registered with another account
                await _check_device_already_registered(db, payload.device, current_user_id=str(user.id))

    _check_ban_status(user)
    if not user.is_active:
        user.is_active = True  # Re-activate on sign-in (un-snooze)
    await db.flush()

    return await _issue_token_pair(
        user_id=str(user.id), db=db, device=payload.device,
        is_new_user=is_new_user, auth_method="google", request=request,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using a refresh token",
)
@limiter.limit("30/minute")
async def refresh_token(request: Request, payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
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

    return await _issue_token_pair(
        user_id=str(stored.user_id), db=db, device=carried_device,
        auth_method="refresh", request=request,
    )


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


@router.get(
    "/login-history",
    summary="Get my login / sign-in history",
)
async def get_login_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns the most recent login events for the authenticated user."""
    from sqlalchemy import desc as _desc
    result = await db.execute(
        select(LoginEvent)
        .where(LoginEvent.user_id == current_user.id)
        .order_by(_desc(LoginEvent.created_at))
        .limit(min(limit, 200))
    )
    events = result.scalars().all()
    return {
        "events": [
            {
                "id": str(e.id),
                "auth_method": e.auth_method,
                "is_new_user": e.is_new_user,
                "ip_address": e.ip_address,
                "user_agent": e.user_agent,
                "device_id": e.device_id,
                "device_model": e.device_model,
                "device_os": e.device_os,
                "device_name": e.device_name,
                "app_version": e.app_version,
                "network_type": e.network_type,
                "carrier": e.carrier,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "total": len(events),
    }
