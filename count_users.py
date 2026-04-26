#!/usr/bin/env python3
"""
Count users in the database (excluding specific email).
"""
import asyncio

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


async def count_users():
    """Count users excluding ak@ailoo.co."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Count total users
        total_result = await session.execute(select(func.count(User.id)))
        total_count = total_result.scalar()
        
        # Count users excluding ak@ailoo.co
        excluded_result = await session.execute(
            select(func.count(User.id)).where(User.email != "ak@ailoo.co")
        )
        excluded_count = excluded_result.scalar()
        
        print(f"\n{'='*60}")
        print(f"Total users in database: {total_count}")
        print(f"Users (excluding ak@ailoo.co): {excluded_count}")
        print(f"{'='*60}\n")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(count_users())
