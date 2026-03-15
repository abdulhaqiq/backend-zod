"""
mini_game.py — MiniGame catalogue + GameResponse records

MiniGame   : one row per game type (wyr, nhi, hot_takes, quiz, build_date, emoji_story)
             also used for question_cards and truth_or_dare
GameCard   : individual playable card (reuses existing `cards` table structure for
             question/tod; new rows for mini-games)
GameResponse: every time a user taps a choice in a game bubble this is persisted
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MiniGame(Base):
    __tablename__ = "mini_games"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # e.g. "wyr" | "nhi" | "hot_takes" | "quiz" | "build_date" | "emoji_story"
    game_type: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    tagline: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emoji: Mapped[str] = mapped_column(String(12), nullable=False)
    accent_color: Mapped[str] = mapped_column(String(12), nullable=False, default="#8b5cf6")
    bg_color: Mapped[str] = mapped_column(String(12), nullable=False, default="#1e1040")
    # ordered list of category names for this game (stored as JSON array)
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GameResponse(Base):
    """Records a player's answer/choice for a specific game message."""
    __tablename__ = "game_responses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The WS message id of the game invite/turn this response belongs to
    game_message_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    game_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    room_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    responder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Flexible payload: {"choice": "A"} | {"have": true} | {"reaction": "🔥 Agree"} | etc.
    response_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
