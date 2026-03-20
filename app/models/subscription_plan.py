import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubscriptionPlan(Base):
    """
    Stores the Pro subscription plans served to the client.

    apple_product_id — matches the App Store Connect product ID exactly.
    The frontend passes this to RevenueCat to initiate the purchase.
    Billing, renewal, and cancellation are all handled by Apple/RevenueCat;
    this table is purely a source-of-truth for plan metadata shown in the UI.
    """
    __tablename__ = "subscription_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)           # e.g. "Pro Monthly"
    apple_product_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    interval: Mapped[str] = mapped_column(String(16), nullable=False)       # "monthly" | "annual"
    price_display: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "£9.99/mo"
    price_usd: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False) # reference USD price
    badge: Mapped[str | None] = mapped_column(String(32), nullable=True)    # e.g. "Best Value"
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    features: Mapped[list | None] = mapped_column(JSONB, nullable=True)     # ["10 super likes/mo", ...]
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
