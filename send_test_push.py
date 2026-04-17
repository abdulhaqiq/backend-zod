"""
Test push notification sender.
Usage:  python3 send_test_push.py [email]
"""
import asyncio, ssl, sys, json, urllib.request, os

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "ak@ailoo.co"


async def get_token(email: str):
    import asyncpg
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "25060")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "defaultdb"),
        ssl=ctx,
    )
    row = await conn.fetchrow(
        "SELECT id, email, push_token FROM users WHERE email=$1", email
    )
    await conn.close()
    return row


async def main():
    row = await get_token(EMAIL)
    if not row:
        print(f"❌ No user found for {EMAIL}")
        return

    push_token = row["push_token"] or ""
    print(f"User:  {row['email']}  ({row['id']})")
    print(f"Token: {push_token or '(none)'}")

    if not push_token:
        print("❌ No push token saved — user hasn't granted notification permission yet.")
        return

    if not push_token.startswith("ExponentPushToken["):
        print(f"⚠️  Expected ExponentPushToken[...], got: {push_token[:40]}…")
        return

    print("Sending via Expo Push Service…")
    payload = json.dumps({
        "to":    push_token,
        "title": "Test Notification 🚀",
        "body":  "Push notifications are working!",
        "data":  {"type": "test"},
        "sound": "default",
    }).encode()

    req = urllib.request.Request(
        "https://exp.host/--/api/v2/push/send",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())

    status = result.get("data", {}).get("status")
    if status == "ok":
        print("✅ Push sent successfully!")
    else:
        print("❌ Push failed:", result)


asyncio.run(main())
