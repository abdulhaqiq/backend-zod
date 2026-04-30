#!/usr/bin/env python3
"""
Delete specific users by email address.
"""
import asyncio

from sqlalchemy import delete, select, func, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


# Emails to delete
EMAILS_TO_DELETE = [
    "ak@ailoo.co",
    "ak.kumshey@gmail.com",
    "abdulkumsyey@gmail.com",
]


async def delete_specific_users():
    """Delete users with specific email addresses."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Count total users before
        total_before_result = await session.execute(select(func.count(User.id)))
        total_before = total_before_result.scalar()

        # Find users to delete
        result = await session.execute(
            select(User.id, User.email, User.full_name)
            .where(User.email.in_(EMAILS_TO_DELETE))
        )
        users_to_delete = result.all()

        print(f"\n{'='*60}")
        print(f"Total users in database (before): {total_before}")
        print(f"{'='*60}\n")

        if not users_to_delete:
            print("No users found with the specified emails.")
            print(f"Searched for: {', '.join(EMAILS_TO_DELETE)}")
            await engine.dispose()
            return

        print(f"Found {len(users_to_delete)} user(s) to delete:")
        for user in users_to_delete:
            print(f"  ✗ {user.email} - {user.full_name or 'N/A'} ({user.id})")

        # Count how many will remain
        remaining_count = total_before - len(users_to_delete)

        print(f"\n⚠️  WARNING: This will DELETE {len(users_to_delete)} user(s)!")
        print(f"⚠️  This action CANNOT be undone!")
        print(f"✓  {remaining_count} user(s) will remain in the database.")
        print("\nProceeding with deletion...")

        # Delete the users
        print("\n🗑️  Deleting users...")
        result = await session.execute(
            delete(User).where(User.email.in_(EMAILS_TO_DELETE))
        )
        await session.commit()

        deleted_count = result.rowcount

        # Count after
        total_after_result = await session.execute(select(func.count(User.id)))
        total_after = total_after_result.scalar()

        print(f"\n{'='*60}")
        print(f"✅ Successfully deleted {deleted_count} user(s).")
        print(f"✅ Remaining users in database: {total_after}")
        print(f"{'='*60}\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(delete_specific_users())
