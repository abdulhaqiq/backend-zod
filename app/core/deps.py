import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

bearer_scheme = HTTPBearer()

_DB_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Service temporarily unavailable. Please try again.",
)


async def _resolve_user(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> User:
    """Decode the JWT and return the User row, raising 401 if invalid."""
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    try:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
    except (asyncio.TimeoutError, TimeoutError, OSError):
        raise _DB_UNAVAILABLE
    except Exception as exc:
        # Catch asyncpg-level errors (no module import needed — match by message)
        if "timeout" in str(exc).lower() or "nodename" in str(exc).lower():
            raise _DB_UNAVAILABLE
        raise

    if user is None:
        raise credentials_exception

    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the JWT and enforce the account is not banned or deleted.

    is_active=False means snooze mode — the user is hidden from the discovery
    feed but can still use the app normally (log in, chat, change settings, etc.).

    is_banned=True is set by admins to fully block a user's API access.
    is_deleted=True means the account was deleted and should be logged out.
    """
    user = await _resolve_user(credentials, db)

    if getattr(user, 'is_deleted', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ACCOUNT_DELETED",
        )

    if getattr(user, 'is_banned', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been suspended.",
        )

    return user


async def get_current_user_allow_inactive(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Alias of get_current_user — kept for backwards compatibility.

    Snooze (is_active=False) no longer blocks API access; only is_banned does.
    This dependency remains so existing endpoint signatures don't need changing.
    """
    return await get_current_user(credentials, db)


async def get_pro_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency that requires a valid Bearer token AND an active paid subscription
    (Pro or Premium+). Raises HTTP 403 for free-tier users.

    Also auto-expires stale paid tiers if subscription_expires_at has passed,
    persisting the downgrade to DB so every subsequent request sees the correct tier.
    """
    tier = current_user.subscription_tier
    expires = current_user.subscription_expires_at

    if tier in ("pro", "premium_plus") and expires is not None:
        now = datetime.now(timezone.utc)
        exp_aware = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
        if exp_aware < now:
            current_user.subscription_tier = "free"
            current_user.subscription_expires_at = None
            await db.commit()
            tier = "free"

    if tier not in ("pro", "premium_plus"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires a Pro or Premium+ subscription. Upgrade to unlock it.",
        )
    return current_user
