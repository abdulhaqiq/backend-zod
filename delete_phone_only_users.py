#!/usr/bin/env python3
"""
Delete all phone-only users (no email = test/seeded data).
"""
import asyncio

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


async def delete_phone_only_users():
    """Delete all phone-only users (no email)."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Get phone-only users (NULL email)
        result = await session.execute(
            select(User.id, User.phone, User.full_name)
            .where(User.email.is_(None))
        )
        users_to_delete = result.all()

        print(f"\n{'='*60}")
        print(f"Phone-only users to DELETE: {len(users_to_delete)}")
        print(f"{'='*60}\n")

        if not users_to_delete:
            print("No phone-only users to delete.")
            await engine.dispose()
            return

        print("Users being DELETED:")
        for user in users_to_delete:
            print(f"  ✗ {user.phone} - {user.full_name or 'N/A'}")

        print(f"\n⚠️  WARNING: This will DELETE {len(users_to_delete)} phone-only user(s)!")
        print(f"⚠️  This action CANNOT be undone!")
        print("\nProceeding with deletion...")

        # Delete users one by one
        deleted_count = 0
        for user in users_to_delete:
            try:
                await session.execute(
                    delete(User).where(User.id == user.id)
                )
                await session.commit()
                deleted_count += 1
                print(f"  Deleted: {user.phone}")
                await asyncio.sleep(0.1)
            except Exception as e:
                await session.rollback()
                print(f"  Error deleting {user.phone}: {e}")

        # Count after
        total_after_result = await session.execute(select(func.count(User.id)))
        total_after = total_after_result.scalar()

        print(f"\n{'='*60}")
        print(f"✅ Successfully deleted {deleted_count} phone-only user(s).")
        print(f"✅ Remaining users: {total_after}")
        print(f"{'='*60}\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(delete_phone_only_users())
