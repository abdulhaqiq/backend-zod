import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    # ── Extra indexes ─────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_users_is_active", "is_active"),
        Index("ix_users_created_at", "created_at"),
        Index("ix_users_is_onboarded", "is_onboarded"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # At least one of email / phone must be set, but neither is required alone
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    phone: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True
    )

    # Social provider IDs
    apple_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    facebook_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )

    # Password only for email/password accounts
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Core profile ──────────────────────────────────────────────────────────
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Onboarding steps ─────────────────────────────────────────────────────
    purpose: Mapped[list | None] = mapped_column(JSONB, nullable=True)         # ["relationship", "friends", ...]
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interests: Mapped[list | None] = mapped_column(JSONB, nullable=True)      # ["hiking", "cooking", ...]
    lifestyle: Mapped[dict | None] = mapped_column(JSONB, nullable=True)      # {exercise, drinking, smoking, ...}
    values_list: Mapped[list | None] = mapped_column(JSONB, nullable=True)    # ["loyalty", "ambition", ...]
    prompts: Mapped[list | None] = mapped_column(JSONB, nullable=True)        # [{question, answer}, ...]
    photos: Mapped[list | None] = mapped_column(JSONB, nullable=True)         # [url, ...]

    # ── Extended profile (About You section) ─────────────────────────────────
    education_level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    looking_for: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family_plans: Mapped[str | None] = mapped_column(String(64), nullable=True)
    have_kids: Mapped[str | None] = mapped_column(String(64), nullable=True)
    star_sign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    religion: Mapped[str | None] = mapped_column(String(64), nullable=True)
    languages: Mapped[list | None] = mapped_column(JSONB, nullable=True)      # ["English", "Spanish"]

    # ── Edit profile sections ─────────────────────────────────────────────────
    voice_prompts: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{topic, url, duration_sec}, ...]
    causes: Mapped[list | None] = mapped_column(JSONB, nullable=True)         # ["Environment", "Education"]
    work_experience: Mapped[list | None] = mapped_column(JSONB, nullable=True) # [{job_title, company, start_year, end_year, current}]
    education: Mapped[list | None] = mapped_column(JSONB, nullable=True)      # [{institution, course, degree, grad_year}]
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hometown: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── Auto-detected location (updated on each app open) ─────────────────────
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)   # formatted address
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Subscription ──────────────────────────────────────────────────────────
    subscription_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")  # free | pro
    subscription_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revenuecat_customer_id: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)

    # ── Preferences ───────────────────────────────────────────────────────────
    dark_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Status flags ─────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_onboarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
