"""
Marketing notification models.

Three tables:
  marketing_countries  — one row per country-timezone pair, with IANA tz and peak hours
  marketing_templates  — multilingual push notification templates
  marketing_campaigns  — send log (manual + scheduler) used for history and dedup
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketingCountry(Base):
    __tablename__ = "marketing_countries"
    __table_args__ = (
        UniqueConstraint("code", "tz_name", name="uq_marketing_country_code_tz"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Display / identification
    name: Mapped[str] = mapped_column(String(128), nullable=False)          # "Saudi Arabia"
    code: Mapped[str] = mapped_column(String(8),   nullable=False, index=True)  # "SA"
    region: Mapped[str] = mapped_column(String(64), nullable=False, index=True) # "GCC"

    # Timezone — IANA name (one row per timezone per country for multi-tz countries)
    tz_name: Mapped[str] = mapped_column(String(64), nullable=False)        # "Asia/Riyadh"

    # Peak local hours to send marketing pushes (list of ints 0–23)
    peak_hours: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Primary language code for template selection fallback (ISO 639-1)
    primary_language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MarketingTemplate(Base):
    __tablename__ = "marketing_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    language_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True, default="en")

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # "promotions" or "dating_tips" — controls user preference gate in push.py
    notif_type: Mapped[str] = mapped_column(String(32), nullable=False, default="promotions")

    # Optional extra data payload delivered to the app
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class MarketingCampaign(Base):
    """
    Send log for every marketing push (manual from admin UI or auto from scheduler).
    Also used for scheduler deduplication: skip if a scheduler send for the same
    (target_value=country_code, tz_name, peak_hour) exists within the last 55 minutes.
    """
    __tablename__ = "marketing_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # FK to template (nullable — custom sends supply title/body directly)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # For custom one-off sends without a stored template
    custom_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    custom_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Targeting
    # "all" | "country" | "region" | "email" | "phone"
    target: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    target_value: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Language used for this send (auto-detected or overridden)
    language_code: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # For scheduler dedup: stores the IANA tz + peak hour that triggered this
    scheduler_tz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scheduler_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Status: "sent" | "failed" | "partial"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="sent")

    # "admin" | "scheduler"
    triggered_by: Mapped[str] = mapped_column(String(32), nullable=False, default="admin")

    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
