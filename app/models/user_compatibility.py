"""
Stores the pairwise compatibility result between two users.

user_a_id is always the smaller UUID (stable canonical ordering) so there is
exactly one row per pair regardless of who viewed whom.

score_hash = MD5(profile_hash_a + profile_hash_b) — if either user updates
their profile the hashes diverge and the next profile view triggers a fresh
computation which overwrites this row.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserCompatibility(Base):
    __tablename__ = "user_compatibility"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_user_compat_pair"),
    )

    id:          Mapped[int]            = mapped_column(primary_key=True, autoincrement=True)
    user_a_id:   Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_b_id:   Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    percent:     Mapped[float]          = mapped_column(Float, nullable=False)
    tier:        Mapped[str]            = mapped_column(String(32), nullable=False)
    breakdown:   Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict)
    insights:    Mapped[list]           = mapped_column(JSONB, nullable=False, default=list)
    brief:       Mapped[str]            = mapped_column(String(512), nullable=False, default="")

    # Invalidation key: MD5 of both users' profile_hash values.
    # Stale if either user has updated their scored profile fields.
    score_hash:  Mapped[str | None]     = mapped_column(String(32), nullable=True)

    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
