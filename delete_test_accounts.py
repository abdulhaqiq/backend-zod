#!/usr/bin/env python3
"""
Delete test/seeded accounts, keeping only real users with genuine emails.
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

# Delete these test emails
TEST_EMAILS = [
    "ak.kumshey@gmail.com",
    "ak@ailoo.co",
    "abdulkumsyey@gmail.com",
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

        # Count real users (with our whitelist emails)
        real_result = await session.execute(
            select(func.count(User.id)).where(User.email.in_(REAL_EMAILS))
        )
        real_count = real_result.scalar()

        # Count test users to delete
        test_email_result = await session.execute(
            select(func.count(User.id)).where(User.email.in_(TEST_EMAILS))
        )
        test_email_count = test_email_result.scalar()

        # Count phone-only users (no email = likely seeded/test)
        phone_only_result = await session.execute(
            select(func.count(User.id)).where(User.email.is_(None))
        )
        phone_only_count = phone_only_result.scalar()

        # Total to delete
        to_delete_count = total_before - real_count

        print(f"\n{'='*60}")
        print(f"Total users in database: {total_before}")
        print(f"Real users (keeping): {real_count}")
        print(f"Test emails to delete: {test_email_count}")
        print(f"Phone-only users to delete: {phone_only_count}")
        print(f"Total to DELETE: {to_delete_count}")
        print(f"{'='*60}\n")

        # Show users being kept
        kept_result = await session.execute(
            select(User.email, User.full_name).where(User.email.in_(REAL_EMAILS))
        )
        print("Users being KEPT:")
        for user in kept_result.all():
            print(f"  ✓ {user.email} - {user.full_name or 'N/A'}")

        # Show users being deleted
        delete_result = await session.execute(
            select(User.email, User.phone, User.full_name, User.created_at)
            .where(~User.email.in_(REAL_EMAILS) | User.email.is_(None))
        )
        print("\nUsers being DELETED:")
        for user in delete_result.all():
            ident = user.email or user.phone
            print(f"  ✗ {ident} - {user.full_name or 'N/A'}")

        if to_delete_count == 0:
            print("\nNo users to delete.")
            await engine.dispose()
            return

        print(f"\n⚠️  WARNING: This will DELETE {to_delete_count} user(s)!")
        print(f"⚠️  Only {real_count} real user(s) will remain.")
        print(f"⚠️  This action CANNOT be undone!")
        print("\nProceeding with deletion...")

        # Delete users NOT in the real emails list
        result = await session.execute(
            delete(User).where(~User.email.in_(REAL_EMAILS))
        )
        await session.commit()

        deleted_count = result.rowcount

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
