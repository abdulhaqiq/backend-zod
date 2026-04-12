"""
Moderation endpoints:
  POST   /moderation/report          — submit a report for another user
  POST   /moderation/block           — block a user (hides them from your feed forever)
  DELETE /moderation/block/{user_id} — unblock a user
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.message import Message
from app.models.user import User
from app.models.user_report import UserReport
from app.services.chat_moderation import check_chat_for_abuse
from app.services.photo_moderation import scan_user_photos

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


# ── helpers ──────────────────────────────────────────────────────────────────

async def _apply_trust_score(reported_user: User, db: AsyncSession) -> None:
    """Evaluate trust_score and enforce bans / device blacklisting."""
    if reported_user.trust_score <= 0:
        reported_user.is_active = False
        _log.warning("User %s banned (trust_score=%s)", reported_user.id, reported_user.trust_score)
    if reported_user.trust_score < 0:
        reported_user.device_blacklisted = True
        _log.warning("Device blacklisted for user %s", reported_user.id)
    await db.commit()


async def _photo_moderation_task(
    reported_user_id: uuid.UUID,
    photo_urls: list[str],
    db_factory,
) -> None:
    """Background: scan photos with Rekognition; ban immediately if flagged."""
    async with db_factory() as db:
        flagged = await scan_user_photos(photo_urls)
        if not flagged:
            return
        result = await db.execute(select(User).where(User.id == reported_user_id))
        user = result.scalar_one_or_none()
        if user:
            user.trust_score = 0
            _log.warning("Rekognition confirmed inappropriate photos for user %s — banning", user.id)
            await _apply_trust_score(user, db)


async def _chat_moderation_task(
    reporter_id: uuid.UUID,
    reported_user_id: uuid.UUID,
    db_factory,
) -> None:
    """Background: check last 50 messages with OpenAI Moderation; ban if flagged."""
    async with db_factory() as db:
        room_id = Message.make_room_id(reporter_id, reported_user_id)
        result = await db.execute(
            select(Message.content)
            .where(Message.room_id == room_id)
            .order_by(Message.created_at.desc())
            .limit(50)
        )
        messages = [row[0] for row in result.fetchall()]
        if not messages:
            return

        flagged = await check_chat_for_abuse(messages)
        if not flagged:
            return

        user_result = await db.execute(select(User).where(User.id == reported_user_id))
        user = user_result.scalar_one_or_none()
        if user:
            user.trust_score = 0
            _log.warning("OpenAI confirmed abuse in chat for user %s — banning", user.id)
            await _apply_trust_score(user, db)


# ── endpoints ─────────────────────────────────────────────────────────────────

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

    # Fetch the reported user
    reported_result = await db.execute(
        select(User).where(User.id == body.reported_id)
    )
    reported_user = reported_result.scalar_one_or_none()
    if not reported_user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Save report
    report = UserReport(
        reporter_id=current_user.id,
        reported_id=body.reported_id,
        reason=body.reason,
        custom_reason=body.custom_reason or None,
    )
    db.add(report)

    # ── Trust score: every report -1 ─────────────────────────────────────────
    reported_user.trust_score -= 1

    # ── Reason-specific logic ─────────────────────────────────────────────────
    if body.reason == "fake_profile":
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        count_result = await db.execute(
            select(func.count()).select_from(UserReport).where(
                UserReport.reported_id == reported_user.id,
                UserReport.reason == "fake_profile",
                UserReport.created_at >= cutoff,
            )
        )
        fake_count = count_result.scalar_one()

        if fake_count >= 3:
            reported_user.trust_score -= 2
            reported_user.face_scan_required = True
            _log.info(
                "User %s hit 3 fake_profile reports — face scan required (trust_score=%s)",
                reported_user.id, reported_user.trust_score,
            )
            # Reset verification so they must re-verify
            if reported_user.is_verified or reported_user.verification_status not in (None, "unverified"):
                reported_user.verification_status = "unverified"
                reported_user.is_verified = False
                _log.info("Verification reset for user %s", reported_user.id)

    elif body.reason == "inappropriate_photos":
        # Collect photo URLs from the reported user's photos JSONB field
        photos_data = reported_user.photos or []
        photo_urls: list[str] = []
        for item in photos_data:
            if isinstance(item, dict):
                url = item.get("url") or item.get("uri") or item.get("src")
                if url:
                    photo_urls.append(url)
            elif isinstance(item, str):
                photo_urls.append(item)

        if photo_urls:
            from app.db.session import get_db as _get_db_ctx
            asyncio.create_task(
                _photo_moderation_task(reported_user.id, photo_urls, _get_db_ctx)
            )

    elif body.reason in {"harassment", "hate_speech", "spam"}:
        from app.db.session import get_db as _get_db_ctx
        asyncio.create_task(
            _chat_moderation_task(current_user.id, reported_user.id, _get_db_ctx)
        )

    # ── Enforce trust score thresholds ────────────────────────────────────────
    await db.commit()
    await db.refresh(reported_user)
    await _apply_trust_score(reported_user, db)

    _log.info(
        "Report submitted: reporter=%s reported=%s reason=%s trust_score_now=%s",
        current_user.id, body.reported_id, body.reason, reported_user.trust_score,
    )
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
