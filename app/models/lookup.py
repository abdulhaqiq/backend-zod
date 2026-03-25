from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RelationshipType(Base):
    __tablename__ = "relationship_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LookupOption(Base):
    """
    Generic lookup table for all profile option lists.
    category values: exercise, education_level, drinking, smoking,
                     looking_for, family_plans, have_kids, star_sign,
                     religion, language
    """
    __tablename__ = "lookup_options"
    __table_args__ = (
        UniqueConstraint("category", "label", name="uq_lookup_category_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    emoji: Mapped[str | None] = mapped_column(String(16), nullable=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
