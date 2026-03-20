import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserScore(Base):
    """
    Stores the 8-category compatibility score for each user.
    Scores range 1-10. Re-computed only when profile_hash changes (i.e. the
    user edited a field that affects scoring). Same hash → cached score returned.
    """
    __tablename__ = "user_scores"

    id:           Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:      Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # ── 8 scoring dimensions (1–10) ───────────────────────────────────────────
    education:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    career:       Mapped[float | None]   = mapped_column(Float, nullable=True)
    lifestyle:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    values:       Mapped[float | None]   = mapped_column(Float, nullable=True)
    interests:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    personality:  Mapped[float | None]   = mapped_column(Float, nullable=True)
    social:       Mapped[float | None]   = mapped_column(Float, nullable=True)
    intentions:   Mapped[float | None]   = mapped_column(Float, nullable=True)

    # ── Composite ─────────────────────────────────────────────────────────────
    overall:      Mapped[float | None]   = mapped_column(Float, nullable=True)

    # ── AI reasoning per category ─────────────────────────────────────────────
    reasoning:    Mapped[dict | None]    = mapped_column(JSONB, nullable=True)

    # ── Change detection hash ─────────────────────────────────────────────────
    # MD5 of the profile fields that affect scoring. If unchanged, skip recompute.
    profile_hash: Mapped[str | None]     = mapped_column(String(32), nullable=True)

    version:      Mapped[int]            = mapped_column(Integer, nullable=False, default=1)
    scored_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
