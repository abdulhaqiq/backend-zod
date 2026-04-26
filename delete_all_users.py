#!/usr/bin/env python3
"""
Delete all users from the database except ak@ailoo.co.
WARNING: This is a destructive operation that cannot be undone.
"""
import asyncio
import sys

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


async def delete_all_users():
    """Delete all users from the database except ak@ailoo.co."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Count total users
        total_result = await session.execute(select(func.count(User.id)))
        total_count = total_result.scalar()
        
        # Count users to be deleted (excluding ak@ailoo.co)
        to_delete_result = await session.execute(
            select(func.count(User.id)).where(User.email != "ak@ailoo.co")
        )
        to_delete_count = to_delete_result.scalar()
        
        print(f"\n{'='*60}")
        print(f"Total users in database: {total_count}")
        print(f"Users to delete (excluding ak@ailoo.co): {to_delete_count}")
        print(f"Users to keep (ak@ailoo.co): {total_count - to_delete_count}")
        print(f"{'='*60}\n")
        
        if to_delete_count == 0:
            print("No users to delete.")
            return
        
        # Ask for confirmation
        print("⚠️  WARNING: This will DELETE ALL USERS except ak@ailoo.co!")
        print("⚠️  This action CANNOT be undone!")
        print("\nType 'DELETE' to confirm: ", end="")
        confirmation = input().strip()
        
        if confirmation != "DELETE":
            print("\n❌ Operation cancelled. No users were deleted.")
            return
        
        # Delete all users except ak@ailoo.co
        print("\n🗑️  Deleting users...")
        result = await session.execute(
            delete(User).where(User.email != "ak@ailoo.co")
        )
        await session.commit()
        
        deleted_count = result.rowcount
        print(f"\n✅ Successfully deleted {deleted_count} users from the database.")
        print(f"✅ User ak@ailoo.co has been preserved.")
    
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(delete_all_users())
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        sys.exit(1)
