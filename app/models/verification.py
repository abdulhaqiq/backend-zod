import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VerificationAttempt(Base):
    __tablename__ = "verification_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # pending | verified | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)

    # face | id  — which verification type this attempt is for
    attempt_type: Mapped[str] = mapped_column(String(8), default="face", nullable=False)

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Audit / device info ───────────────────────────────────────────────────
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ios | android

    # ── Face scan result ──────────────────────────────────────────────────────
    selfie_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_live: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    face_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    age_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── ID verification result ────────────────────────────────────────────────
    id_front_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    id_back_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    id_face_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    id_text_detected: Mapped[str | None] = mapped_column(Text, nullable=True)   # raw OCR dump
    id_has_name: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_has_dob: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_has_expiry: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_has_number: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Deep-check: does the name/birth-year on the ID match the user's profile?
    id_name_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_dob_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── Shared result ─────────────────────────────────────────────────────────
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
