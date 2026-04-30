import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


async def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that rejects any non-admin caller with a 403."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete the current user's account")
async def delete_my_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Marks the account as deleted (soft delete).

    - All profile data is retained in the database so the phone/social ID
      can be re-used for a fresh sign-up later.
    - On re-registration with the same phone/Apple/Facebook ID the row is
      automatically reset to a blank profile and onboarding is restarted.
    - All active refresh tokens are revoked immediately.
    """
    from app.models.refresh_token import RefreshToken  # local import avoids circular

    now = datetime.now(timezone.utc)
    current_user.is_deleted  = True
    current_user.deleted_at  = now
    current_user.is_active   = False

    # Revoke every refresh token belonging to this user so existing sessions
    # immediately stop working.
    await db.execute(
        delete(RefreshToken).where(RefreshToken.user_id == str(current_user.id))
    )
    await db.commit()
    
    # Send WebSocket notification to force immediate logout on all devices
    try:
        from app.api.v1.endpoints.chat import notify_manager
        nm = notify_manager
        await nm.send_to(str(current_user.id), {
            "type": "account_deleted",
            "message": "Your account has been deleted. You will be logged out.",
            "force_logout": True,
        })
    except Exception:
        pass


@router.get("/", response_model=list[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    result = await db.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    # Revoke all refresh tokens
    from app.models.refresh_token import RefreshToken
    await db.execute(
        delete(RefreshToken).where(RefreshToken.user_id == str(user_id))
    )
    
    # Delete the user
    await db.delete(user)
    await db.commit()
    
    # Send WebSocket notification to force immediate logout on all devices
    try:
        from app.api.v1.endpoints.chat import notify_manager
        nm = notify_manager
        await nm.send_to(str(user_id), {
            "type": "account_deleted",
            "message": "Your account has been deleted by an administrator.",
            "force_logout": True,
        })
    except Exception:
        pass
