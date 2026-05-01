#!/usr/bin/env python3
"""
Delete test/seeded accounts one by one to avoid deadlocks.
"""
import asyncio

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


# Keep only these real user emails
REAL_EMAILS = [
    "arnoldemmi@gmail.com",
    "ziazaiab925@gmail.com",
    "eswarmuppavarapu98@gmail.com",
    "gucciyamino@gmail.com",
    "mr.habuju@gmail.com",
]


async def delete_test_accounts():
    """Delete all test/seeded accounts, keep only real users."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Count total before
        total_before_result = await session.execute(select(func.count(User.id)))
        total_before = total_before_result.scalar()

        # Get users to delete (not in real emails list)
        result = await session.execute(
            select(User.id, User.email, User.phone, User.full_name)
            .where(~User.email.in_(REAL_EMAILS))
        )
        users_to_delete = result.all()

        # Count real users
        real_count = total_before - len(users_to_delete)

        print(f"\n{'='*60}")
        print(f"Total users in database: {total_before}")
        print(f"Real users (keeping): {real_count}")
        print(f"Test users to DELETE: {len(users_to_delete)}")
        print(f"{'='*60}\n")

        if not users_to_delete:
            print("No test users to delete.")
            await engine.dispose()
            return

        # Show users being kept
        kept_result = await session.execute(
            select(User.email, User.full_name).where(User.email.in_(REAL_EMAILS))
        )
        print("Users being KEPT:")
        for user in kept_result.all():
            print(f"  ✓ {user.email} - {user.full_name or 'N/A'}")

        print("\nUsers being DELETED:")
        for user in users_to_delete:
            ident = user.email or user.phone
            print(f"  ✗ {ident} - {user.full_name or 'N/A'}")

        print(f"\n⚠️  WARNING: This will DELETE {len(users_to_delete)} user(s)!")
        print(f"⚠️  Only {real_count} real user(s) will remain.")
        print(f"⚠️  This action CANNOT be undone!")
        print("\nProceeding with deletion...")

        # Delete users one by one to avoid deadlocks
        deleted_count = 0
        for user in users_to_delete:
            try:
                await session.execute(
                    delete(User).where(User.id == user.id)
                )
                await session.commit()
                deleted_count += 1
                ident = user.email or user.phone
                print(f"  Deleted: {ident}")
                # Small delay to avoid overwhelming the database
                await asyncio.sleep(0.1)
            except Exception as e:
                await session.rollback()
                ident = user.email or user.phone
                print(f"  Error deleting {ident}: {e}")

        # Count after
        total_after_result = await session.execute(select(func.count(User.id)))
        total_after = total_after_result.scalar()

        print(f"\n{'='*60}")
        print(f"✅ Successfully deleted {deleted_count} test user(s).")
        print(f"✅ Remaining real users: {total_after}")
        print(f"{'='*60}\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(delete_test_accounts())
