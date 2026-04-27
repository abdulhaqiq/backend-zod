#!/usr/bin/env python3
"""
List all users in the database.
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.user import User


async def list_users():
    """List all users in the database."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # Get all users
        result = await session.execute(
            select(User.id, User.email, User.phone, User.full_name, User.created_at)
            .order_by(User.created_at.desc())
        )
        users = result.all()
        
        print(f"\n{'='*80}")
        print(f"Total users in database: {len(users)}")
        print(f"{'='*80}\n")
        
        if not users:
            print("No users found.")
            return
        
        # Print user details
        for idx, user in enumerate(users, 1):
            print(f"{idx}. ID: {user.id}")
            print(f"   Email: {user.email or 'N/A'}")
            print(f"   Phone: {user.phone or 'N/A'}")
            print(f"   Name: {user.full_name or 'N/A'}")
            print(f"   Created: {user.created_at}")
            print()
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(list_users())
