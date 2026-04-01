"""
Moderation endpoints:
  POST   /moderation/report          — submit a report for another user
  POST   /moderation/block           — block a user (hides them from your feed forever)
  DELETE /moderation/block/{user_id} — unblock a user
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_report import UserReport

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/moderation", tags=["moderation"])

VALID_REPORT_REASONS = {
    "fake_profile",
    "inappropriate_photos",
    "harassment",
    "spam",
    "underage",
    "hate_speech",
    "scam",
    "other",
    "blocked_by_user",   # auto-generated when a user blocks someone
}


class ReportRequest(BaseModel):
    reported_id: str
    reason: str
    custom_reason: str | None = None


class BlockRequest(BaseModel):
    blocked_id: str


@router.post("/report", status_code=status.HTTP_201_CREATED, summary="Report a user")
async def report_user(
    body: ReportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.reported_id == str(current_user.id):
        raise HTTPException(status_code=400, detail="You cannot report yourself.")
    if body.reason not in VALID_REPORT_REASONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid reason. Allowed: {sorted(VALID_REPORT_REASONS)}",
        )

    report = UserReport(
        reporter_id=current_user.id,
        reported_id=body.reported_id,
        reason=body.reason,
        custom_reason=body.custom_reason or None,
    )
    db.add(report)
    await db.commit()
    _log.info("Report submitted: reporter=%s reported=%s reason=%s", current_user.id, body.reported_id, body.reason)
    return {"detail": "Report submitted. Thank you for keeping our community safe."}


@router.post("/block", status_code=status.HTTP_201_CREATED, summary="Block a user")
async def block_user(
    body: BlockRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.blocked_id == str(current_user.id):
        raise HTTPException(status_code=400, detail="You cannot block yourself.")

    # Fetch the blocked user so we can capture their current device_id.
    # This means even if they delete their account and create a new one from
    # the same physical device, they are still excluded from the blocker's feed.
    blocked_result = await db.execute(
        select(User).where(User.id == body.blocked_id)
    )
    blocked_user = blocked_result.scalar_one_or_none()
    blocked_device_id = blocked_user.device_id if blocked_user else None

    await db.execute(
        text("""
            INSERT INTO user_blocks (blocker_id, blocked_id, blocked_device_id)
            VALUES (CAST(:blocker AS uuid), CAST(:blocked AS uuid), :device_id)
            ON CONFLICT (blocker_id, blocked_id) DO UPDATE
                SET blocked_device_id = EXCLUDED.blocked_device_id
        """),
        {
            "blocker":    str(current_user.id),
            "blocked":    body.blocked_id,
            "device_id":  blocked_device_id,
        },
    )

    # Also create a moderation report so admins can see who is being blocked
    # and act within 24 hours per App Store guideline 1.2.
    report = UserReport(
        reporter_id=current_user.id,
        reported_id=body.blocked_id,
        reason="blocked_by_user",
        custom_reason="User was blocked — automatically flagged for moderation review.",
    )
    db.add(report)

    await db.commit()
    _log.info(
        "Block recorded: blocker=%s blocked=%s device=%s",
        current_user.id, body.blocked_id, blocked_device_id or "unknown",
    )
    return {"detail": "User blocked."}


@router.delete("/block/{blocked_id}", status_code=status.HTTP_200_OK, summary="Unblock a user")
async def unblock_user(
    blocked_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("""
            DELETE FROM user_blocks
            WHERE blocker_id = CAST(:blocker AS uuid)
              AND blocked_id = CAST(:blocked AS uuid)
        """),
        {"blocker": str(current_user.id), "blocked": blocked_id},
    )
    await db.commit()
    return {"detail": "User unblocked."}
