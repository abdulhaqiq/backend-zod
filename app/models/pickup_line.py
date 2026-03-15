import uuid

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PickupLine(Base):
    __tablename__ = "pickup_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # "Classic" | "Cheesy" | "Romantic" | "Nerdy" | "Adventurous" | "Deep" | "Funny" | "Smooth"
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    emoji: Mapped[str] = mapped_column(String(10), nullable=False, default="✨")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
