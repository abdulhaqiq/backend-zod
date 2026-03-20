import secrets
import string
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Only alphanumeric uppercase, no ambiguous chars (0/O, 1/I/L)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_MAX_REDEMPTIONS_PER_USER = 2


def _generate_code() -> str:
    """
    Generate a cryptographically secure 16-character gift card code.
    Format: XXXX-XXXX-XXXX-XXXX  (16 chars, 4 groups of 4, no ambiguous chars)
    Example: A3KP-7MNQ-Z9TW-2BXV
    """
    chars = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(16))
    return f"{chars[0:4]}-{chars[4:8]}-{chars[8:12]}-{chars[12:16]}"


class GiftCard(Base):
    """
    A gift card that grants Pro access when redeemed.

    Security constraints (enforced at endpoint level):
      • Only weekly (14 days) or monthly (30 days) plans allowed.
      • Each user account may redeem at most 2 gift cards total.
      • Redemption device_id + IP address are recorded for audit.
      • Code is a 16-char cryptographically random string (groups of 4).
      • Code has a mandatory expiry — admin sets it at creation.

    Lifecycle:
        created  → is_redeemed=False
        redeemed → is_redeemed=True, redeemed_* fields populated
    """
    __tablename__ = "gift_cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 16-char code, e.g. "A3KP-7MNQ-Z9TW-2BXV"
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)

    # ── Plan linkage ──────────────────────────────────────────────────────────
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Allowed intervals: "weekly" | "monthly" only
    plan_interval: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")

    # Days of Pro access this card grants (14 for weekly, 30 for monthly)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # ── Ownership ─────────────────────────────────────────────────────────────
    purchased_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Redemption ────────────────────────────────────────────────────────────
    redeemed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_redeemed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Security: device + IP captured at redemption time
    redeemed_device_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    redeemed_ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── Code expiry (mandatory — None = never, but admin should always set) ───
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Internal note
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    plan         = relationship("SubscriptionPlan", lazy="select", foreign_keys=[plan_id])
    purchased_by = relationship("User",             lazy="select", foreign_keys=[purchased_by_user_id])
    redeemed_by  = relationship("User",             lazy="select", foreign_keys=[redeemed_by_user_id])
