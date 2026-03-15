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
    gender_id: Mapped[int | None] = mapped_column(Integer, nullable=True)     # FK → lookup_options (category=gender)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Onboarding steps ─────────────────────────────────────────────────────
    purpose: Mapped[list | None] = mapped_column(JSONB, nullable=True)        # [relationship_types.id, ...]
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interests: Mapped[list | None] = mapped_column(JSONB, nullable=True)      # [lookup_options.id, ...] (category=interests)
    lifestyle: Mapped[dict | None] = mapped_column(JSONB, nullable=True)      # {drinking: id, smoking: id, exercise: id, diet: id}
    values_list: Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id, ...] (category=values_list)
    prompts: Mapped[list | None] = mapped_column(JSONB, nullable=True)        # [{question, answer}, ...]
    photos: Mapped[list | None] = mapped_column(JSONB, nullable=True)         # [url, ...]

    # ── Extended profile (About You section) ─────────────────────────────────
    education_level_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK → lookup_options (category=education_level)
    looking_for_id: Mapped[int | None] = mapped_column(Integer, nullable=True)      # FK → lookup_options (category=looking_for)
    family_plans_id: Mapped[int | None] = mapped_column(Integer, nullable=True)     # FK → lookup_options (category=family_plans)
    have_kids_id: Mapped[int | None] = mapped_column(Integer, nullable=True)        # FK → lookup_options (category=have_kids)
    star_sign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)        # FK → lookup_options (category=star_sign)
    religion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)         # FK → lookup_options (category=religion)
    ethnicity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)        # FK → lookup_options (category=ethnicity)
    languages: Mapped[list | None] = mapped_column(JSONB, nullable=True)            # [lookup_options.id, ...] (category=language)

    # ── Edit profile sections ─────────────────────────────────────────────────
    voice_prompts: Mapped[list | None] = mapped_column(JSONB, nullable=True)   # [{topic, url, duration_sec}, ...]
    causes: Mapped[list | None] = mapped_column(JSONB, nullable=True)          # [lookup_options.id, ...] (category=causes)
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
    best_photo_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Zod Work profile ──────────────────────────────────────────────────────
    work_photos: Mapped[list | None] = mapped_column(JSONB, nullable=True)                   # [url, ...]
    work_prompts: Mapped[list | None] = mapped_column(JSONB, nullable=True)                  # [{question, answer}, ...]
    work_matching_goals: Mapped[list | None] = mapped_column(JSONB, nullable=True)           # [lookup_options.id, ...] (category=work_matching_goals)
    work_are_you_hiring: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    work_commitment_level_id: Mapped[int | None] = mapped_column(Integer, nullable=True)     # FK → lookup_options (category=work_commitment_level)
    work_skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)                   # [lookup_options.id, ...] (category=work_skills)
    work_equity_split_id: Mapped[int | None] = mapped_column(Integer, nullable=True)         # FK → lookup_options (category=work_equity_split)
    work_industries: Mapped[list | None] = mapped_column(JSONB, nullable=True)               # [lookup_options.id, ...] (category=work_industries)
    work_scheduling_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    work_who_to_show_id: Mapped[int | None] = mapped_column(Integer, nullable=True)          # FK → lookup_options (category=work_who_to_show)
    work_priority_startup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── Push notifications ────────────────────────────────────────────────────
    push_token: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Discover filter preferences ───────────────────────────────────────────
    filter_age_min:         Mapped[int | None]  = mapped_column(Integer, nullable=True)
    filter_age_max:         Mapped[int | None]  = mapped_column(Integer, nullable=True)
    filter_max_distance_km: Mapped[int | None]  = mapped_column(Integer, nullable=True)  # null = no limit
    filter_verified_only:   Mapped[bool]        = mapped_column(Boolean, default=False, nullable=False)
    filter_star_signs:      Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_interests:       Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_languages:       Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    # Pro-only filters
    filter_purpose:         Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [relationship_types.id]
    filter_looking_for:     Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_education_level: Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_family_plans:    Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_have_kids:       Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_ethnicities:     Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_exercise:        Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_drinking:        Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_smoking:         Mapped[list | None] = mapped_column(JSONB, nullable=True)    # [lookup_options.id]
    filter_height_min:      Mapped[int | None]  = mapped_column(Integer, nullable=True)  # cm
    filter_height_max:      Mapped[int | None]  = mapped_column(Integer, nullable=True)  # cm

    # ── Mood / vibe status ────────────────────────────────────────────────────
    mood_emoji:  Mapped[str | None] = mapped_column(String(8),   nullable=True)  # e.g. "🎉"
    mood_text:   Mapped[str | None] = mapped_column(String(60),  nullable=True)  # e.g. "Up for a coffee chat"

    # ── Verification ──────────────────────────────────────────────────────────
    face_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0–100.0 %
    # unverified | pending | verified | rejected
    verification_status: Mapped[str] = mapped_column(
        String(16), default="unverified", nullable=False
    )

    # ── Status flags ─────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_onboarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
