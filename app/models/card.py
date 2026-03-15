import uuid

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # "question" | "truth_or_dare"
    game: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # "Deep" | "Fun" | "Would You Rather" | "Truth" | "Dare"
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(40), nullable=False)
    emoji: Mapped[str] = mapped_column(String(10), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Dark background color for card
    color: Mapped[str] = mapped_column(String(10), nullable=False, default="#1a1a2e")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
