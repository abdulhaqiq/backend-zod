#!/usr/bin/env python3
"""
Clear all user activity: messages, user_scores, and any remaining likes/matches/swipes.
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


TABLES_TO_CLEAR = [
    'likes',
    'matches',
    'swipes',
    'user_blocks',
    'messages',
    'user_compatibility',
    'user_scores',
    'message_reactions',
    'game_responses',
]


async def clear_all_activity():
    """Clear all user activity data."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        print(f"\n{'='*60}")
        print("Clearing all user activity data...")
        print(f"{'='*60}\n")

        # Show counts before
        print("BEFORE:")
        total_before = 0
        for table in TABLES_TO_CLEAR:
            try:
                result = await session.execute(text(f'SELECT COUNT(*) FROM {table}'))
                count = result.scalar()
                total_before += count
                print(f'  {table}: {count}')
            except Exception as e:
                print(f'  {table}: Error - {e}')

        print(f"\n⚠️  WARNING: This will DELETE {total_before} records!")
        print(f"⚠️  This action CANNOT be undone!")
        print("\nProceeding with deletion...\n")

        # Clear tables one by one
        total_deleted = 0
        for table in TABLES_TO_CLEAR:
            try:
                result = await session.execute(text(f'DELETE FROM {table}'))
                await session.commit()
                deleted = result.rowcount
                total_deleted += deleted
                print(f'  ✓ Deleted {deleted} from {table}')
            except Exception as e:
                await session.rollback()
                print(f'  ✗ Error clearing {table}: {e}')

        # Show counts after
        print("\nAFTER:")
        total_after = 0
        for table in TABLES_TO_CLEAR:
            try:
                result = await session.execute(text(f'SELECT COUNT(*) FROM {table}'))
                count = result.scalar()
                total_after += count
                print(f'  {table}: {count}')
            except Exception as e:
                print(f'  {table}: Error - {e}')

        print(f"\n{'='*60}")
        print(f"✅ Successfully deleted {total_deleted} records.")
        print(f"{'='*60}\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(clear_all_activity())
