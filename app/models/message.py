import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # "min(sender_id, receiver_id):max(sender_id, receiver_id)" for O(1) room lookup
    room_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)

    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    receiver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    msg_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # card/answer metadata

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @staticmethod
    def make_room_id(uid1: uuid.UUID, uid2: uuid.UUID) -> str:
        a, b = str(uid1), str(uid2)
        return f"{min(a, b)}:{max(a, b)}"
