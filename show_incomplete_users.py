"""
Show users who haven't completed onboarding.
"""
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.user import User


async def get_incomplete_users():
    async with AsyncSessionLocal() as db:
        # Users who haven't onboarded
        result = await db.execute(
            select(User).where(
                User.is_deleted.is_(False),
                User.is_onboarded.is_(False),
            ).order_by(User.created_at.desc())
        )
        users = result.scalars().all()
        
        print("\n" + "="*80)
        print("USERS NOT YET ONBOARDED")
        print("="*80)
        
        if not users:
            print("\nAll users have completed onboarding!")
        else:
            for i, user in enumerate(users, 1):
                print(f"\n{i}. User ID: {user.id}")
                print(f"   Name:     {user.full_name or 'Not set'}")
                print(f"   Email:    {user.email or 'Not set'}")
                print(f"   Phone:    {user.phone or 'Not set'}")
                print(f"   Apple:    {'Yes' if user.apple_id else 'No'}")
                print(f"   Google:   {'Yes' if user.google_id else 'No'}")
                print(f"   Created:  {user.created_at.strftime('%Y-%m-%d %H:%M')}")
                print(f"   Gender:   {user.gender_id if user.gender_id else 'Not set'}")
                print(f"   DOB:      {user.date_of_birth if user.date_of_birth else 'Not set'}")
                print(f"   Photos:   {len(user.photos) if user.photos else 0}")
        
        print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(get_incomplete_users())
