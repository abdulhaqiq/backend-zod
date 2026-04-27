#!/usr/bin/env python3
"""
Delete test/seeded users, keeping only users with valid email addresses.
"""
import asyncio

from sqlalchemy import delete, select, func, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


async def delete_test_users():
    """Delete all users without email addresses (test/seed data)."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Count total users
        total_result = await session.execute(select(func.count(User.id)))
        total_count = total_result.scalar()
        
        # Count users with emails (real users)
        with_email_result = await session.execute(
            select(func.count(User.id)).where(User.email != None)
        )
        with_email_count = with_email_result.scalar()
        
        to_delete_count = total_count - with_email_count
        
        print(f"\n{'='*60}")
        print(f"Total users in database: {total_count}")
        print(f"Real users (with email): {with_email_count}")
        print(f"Test users to delete (no email): {to_delete_count}")
        print(f"{'='*60}\n")
        
        if to_delete_count == 0:
            print("No test users to delete.")
            return
        
        # Show which users will be kept
        kept_users = await session.execute(
            select(User.email, User.full_name).where(User.email != None)
        )
        print("Users that will be KEPT:")
        for user in kept_users.all():
            print(f"  ✓ {user.email} - {user.full_name}")
        
        print("\n⚠️  WARNING: This will DELETE all test/seeded users!")
        print("⚠️  This action CANNOT be undone!")
        print("\nType 'DELETE' to confirm: ", end="")
        confirmation = input().strip()
        
        if confirmation != "DELETE":
            print("\n❌ Operation cancelled. No users were deleted.")
            return
        
        # Delete all users without email
        print("\n🗑️  Deleting test users...")
        result = await session.execute(
            delete(User).where(User.email == None)
        )
        await session.commit()
        
        deleted_count = result.rowcount
        print(f"\n✅ Successfully deleted {deleted_count} test users.")
        print(f"✅ {with_email_count} real users preserved.")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(delete_test_users())
