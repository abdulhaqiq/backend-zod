import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserScore(Base):
    """
    Stores the 8-category compatibility score for each user.
    Scores range 1-10. Re-computed whenever the user updates their profile.
    New users start with overall=10 (blank slate — maximum potential).
    """
    __tablename__ = "user_scores"

    id:          Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:     Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # ── 8 scoring dimensions (1–10) ───────────────────────────────────────────
    education:   Mapped[float | None]   = mapped_column(Float, nullable=True)   # education background & intellectual curiosity
    career:      Mapped[float | None]   = mapped_column(Float, nullable=True)   # career ambition & work experience
    lifestyle:   Mapped[float | None]   = mapped_column(Float, nullable=True)   # exercise, diet, drinking, smoking habits
    values:      Mapped[float | None]   = mapped_column(Float, nullable=True)   # religion, family plans, causes, core values
    interests:   Mapped[float | None]   = mapped_column(Float, nullable=True)   # hobbies, passions, travel, activities
    personality: Mapped[float | None]   = mapped_column(Float, nullable=True)   # bio + prompt answers depth & warmth
    social:      Mapped[float | None]   = mapped_column(Float, nullable=True)   # languages, community, communication
    intentions:  Mapped[float | None]   = mapped_column(Float, nullable=True)   # purpose clarity & relationship goals

    # ── Composite ─────────────────────────────────────────────────────────────
    overall:     Mapped[float | None]   = mapped_column(Float, nullable=True)   # weighted average

    # ── AI reasoning per category ──────────────────────────────────────────────
    reasoning:   Mapped[dict | None]    = mapped_column(JSONB, nullable=True)   # {category: "explanation"}

    version:     Mapped[int]            = mapped_column(Integer, nullable=False, default=1)
    scored_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
