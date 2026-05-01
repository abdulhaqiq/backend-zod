#!/usr/bin/env python3
"""
Check user verification status in database.
Usage: python check_user_verification.py abdulkumshey@gmail.com
"""
import asyncio
import sys
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.verification import VerificationAttempt


async def check_user(email: str):
    async with AsyncSessionLocal() as db:
        # Find user by email
        result = await db.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()

        if not user:
            print(f"❌ User not found: {email}")
            return

        print(f"\n👤 User: {user.full_name} ({email})")
        print(f"   ID: {user.id}")
        print(f"\n📊 Verification Status:")
        print(f"   - is_verified: {user.is_verified}")
        print(f"   - verification_status: {user.verification_status}")
        print(f"   - face_scan_required: {user.face_scan_required}")
        print(f"   - face_match_score: {user.face_match_score}")

        # Check latest verification attempts
        result = await db.execute(
            select(VerificationAttempt)
            .where(VerificationAttempt.user_id == user.id)
            .where(VerificationAttempt.attempt_type == "face")
            .order_by(VerificationAttempt.submitted_at.desc())
            .limit(3)
        )
        attempts = result.scalars().all()

        if attempts:
            print(f"\n📸 Last {len(attempts)} Face Verification Attempt(s):")
            for i, a in enumerate(attempts, 1):
                print(f"\n   Attempt #{i}:")
                print(f"   - ID: {a.id}")
                print(f"   - Status: {a.status}")
                print(f"   - Submitted: {a.submitted_at}")
                print(f"   - Processed: {a.processed_at or 'Not yet'}")
                print(f"   - Face match score: {a.face_match_score}")
                print(f"   - Rejection reason: {a.rejection_reason or 'N/A'}")
                print(f"   - Is live: {a.is_live}")
        else:
            print("\n📸 No face verification attempts found")

        # Check ID verification attempts
        result = await db.execute(
            select(VerificationAttempt)
            .where(VerificationAttempt.user_id == user.id)
            .where(VerificationAttempt.attempt_type == "id")
            .order_by(VerificationAttempt.submitted_at.desc())
            .limit(3)
        )
        id_attempts = result.scalars().all()

        if id_attempts:
            print(f"\n🆔 Last {len(id_attempts)} ID Verification Attempt(s):")
            for i, a in enumerate(id_attempts, 1):
                print(f"\n   Attempt #{i}:")
                print(f"   - ID: {a.id}")
                print(f"   - Status: {a.status}")
                print(f"   - Submitted: {a.submitted_at}")
                print(f"   - Processed: {a.processed_at or 'Not yet'}")
        else:
            print("\n🆔 No ID verification attempts found")


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "abdulkumshey@gmail.com"
    asyncio.run(check_user(email))
