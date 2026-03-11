import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OtpCode(Base):
    __tablename__ = "otp_codes"

    # ── Composite & extra indexes ─────────────────────────────────────────────
    __table_args__ = (
        # verify-otp: WHERE phone=? AND verified_at IS NULL AND expires_at > now() ORDER BY created_at DESC
        Index("ix_otp_phone_expires", "phone", "expires_at"),
        # send-rate-limit: WHERE phone=? AND created_at >= one_hour_ago
        Index("ix_otp_phone_created", "phone", "created_at"),
        # block check: WHERE blocked_until IS NOT NULL AND blocked_until > now()
        Index("ix_otp_blocked_until", "blocked_until"),
        # filter on unverified rows
        Index("ix_otp_verified_at", "verified_at"),
        # device monitoring: find all OTPs from a device
        Index("ix_otp_device_id", "device_id"),
        # ip-based monitoring
        Index("ix_otp_ip_address", "ip_address"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # "sms" | "whatsapp"

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Rate-limiting & blocking
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    send_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Device fingerprint & network (all optional — sent from frontend)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)   # iOS vendor ID / Android ID
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_os: Mapped[str | None] = mapped_column(String(64), nullable=True)    # e.g. "iOS 17.4"
    network_type: Mapped[str | None] = mapped_column(String(32), nullable=True) # "wifi" | "cellular" | "unknown"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
