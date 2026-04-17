import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginEvent(Base):
    """Immutable audit log — one row per sign-in / token-issue attempt."""

    __tablename__ = "login_events"

    __table_args__ = (
        Index("ix_le_user_id",    "user_id"),
        Index("ix_le_created_at", "created_at"),
        Index("ix_le_device_id",  "device_id"),
        Index("ix_le_ip_address", "ip_address"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Who ───────────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # nullable: failed logins before user is resolved still get logged

    # ── How ───────────────────────────────────────────────────────────────────
    auth_method: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "phone_otp" | "google" | "apple" | "facebook" | "refresh"

    is_new_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Network — server-derived (trusted) ───────────────────────────────────
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Device — client-reported ──────────────────────────────────────────────
    device_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(255), nullable=True)   # e.g. "iPhone 15 Pro"
    device_os: Mapped[str | None] = mapped_column(String(128), nullable=True)      # e.g. "iOS 17.4"
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)    # user's device nickname
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)     # e.g. "1.2.3"

    # ── Network — client-reported ─────────────────────────────────────────────
    network_type: Mapped[str | None] = mapped_column(String(32), nullable=True)    # "wifi" | "cellular" | "unknown"
    carrier: Mapped[str | None] = mapped_column(String(128), nullable=True)        # SIM carrier name, e.g. "Airtel"

    # ── When ──────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
