"""
Profile endpoints:
  GET  /profile/me   — return current user's full profile
  PATCH /profile/me  — update any subset of profile fields (onboarding + edit)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.profile import MeResponse, ProfileUpdateRequest

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=MeResponse, summary="Get current user profile")
async def get_me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse.model_validate(current_user)


@router.patch("/me", response_model=MeResponse, summary="Update profile (onboarding or edit)")
async def update_me(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    update_data = payload.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update.",
        )

    for field, value in update_data.items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)
    return MeResponse.model_validate(current_user)
