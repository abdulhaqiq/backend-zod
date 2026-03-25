"""
Lookup / reference-data endpoints (no auth required).
  GET /lookup/relationship-types  — list selectable relationship intent options
  GET /lookup/options             — all lookup options, optionally filtered by ?category=
  GET /lookup/options/{category}  — options for a single category
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.lookup import LookupOption, RelationshipType

router = APIRouter(prefix="/lookup", tags=["lookup"])


@router.get("/relationship-types", summary="List relationship type options")
async def get_relationship_types(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RelationshipType)
        .where(RelationshipType.is_active.is_(True))
        .order_by(RelationshipType.sort_order)
    )
    types = result.scalars().all()
    return [{"value": t.value, "label": t.label} for t in types]


@router.get("/options", summary="All lookup options (optionally filtered by category)")
async def get_options(
    category: Optional[str] = Query(None, description="Filter by category"),
    db: AsyncSession = Depends(get_db),
):
    q = select(LookupOption).where(LookupOption.is_active.is_(True))
    if category:
        q = q.where(LookupOption.category == category)
    q = q.order_by(LookupOption.category, LookupOption.sort_order)
    result = await db.execute(q)
    rows = result.scalars().all()
    # Group by category → { category: [{id, emoji, label}] }
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row.category, []).append({
            "id": row.id,
            "category": row.category,
            "emoji": row.emoji,
            "label": row.label,
            "subcategory": row.subcategory,
        })
    if category:
        return grouped.get(category, [])
    return grouped


@router.get("/options/{category}", summary="Options for a single category")
async def get_options_by_category(category: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(LookupOption)
        .where(LookupOption.category == category, LookupOption.is_active.is_(True))
        .order_by(LookupOption.sort_order)
    )
    rows = result.scalars().all()
    return [{"id": r.id, "category": r.category, "emoji": r.emoji, "label": r.label, "subcategory": r.subcategory} for r in rows]
