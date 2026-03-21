"""
Truth-or-Dare rounds table.

One row per game round:  invite → choice → question → answer/skip

Lifecycle
---------
tod_invite  →  status = 'invited'
tod_choice  →  status = 'choice_made',  choice set
tod_next    →  status = 'question_sent', question / emoji / category set
tod_answer  →  status = 'answered',      answer set, completed_at set
tod_skip    →  status = 'skipped',       completed_at set
new invite  →  previous active rounds → status = 'expired'

Active game: within 12 hours of the tod_invite.  Only one skip allowed per
room per active game window (enforced on the frontend; backend records it).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TodRound(Base):
    __tablename__ = "tod_rounds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The chat room this game belongs to
    room_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)

    # The original tod_invite message that started this round
    invite_msg_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Who sent the invite (question sender)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Who received the invite (question answerer)
    receiver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # 'truth' | 'dare' — set when partner sends tod_choice
    choice: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # The actual question — set when sender sends tod_next
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_emoji: Mapped[str | None] = mapped_column(String(16), nullable=True)
    question_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_msg_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # The answer — set when receiver sends tod_answer
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_msg_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # 'invited' | 'choice_made' | 'question_sent' | 'answered'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="invited")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
