import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

bearer_scheme = HTTPBearer()


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

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the JWT and enforce that the account is active (not admin-disabled)."""
    user = await _resolve_user(credentials, db)

    # is_active=False via admin ban — block access.
    # Note: snooze mode also sets is_active=False, but that endpoint uses
    # get_current_user_allow_inactive so snoozed users can still toggle themselves back.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    return user


async def get_current_user_allow_inactive(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the JWT without enforcing is_active.

    Use this for endpoints that snoozed users must still be able to reach
    (e.g. the snooze toggle itself, so they can turn snooze back off).
    """
    return await _resolve_user(credentials, db)


async def get_pro_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Dependency that requires a valid Bearer token AND an active Pro subscription.

    Raises HTTP 403 for any authenticated user whose subscription_tier is not 'pro'.
    Use this on every endpoint that is a Pro-only feature so that backend
    enforcement is independent of any frontend checks.
    """
    if current_user.subscription_tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires a Pro subscription. Upgrade to unlock it.",
        )
    return current_user
