"""
Facebook Sign In server-side verification.

Steps:
1. Use the app access token to call /debug_token to verify the user token is genuine
   and was issued for our app.
2. Call /me to fetch the user's profile.
"""
import httpx

from app.core.config import settings

GRAPH_URL = "https://graph.facebook.com/v20.0"


async def verify_facebook_token(access_token: str) -> dict:
    """
    Verify a Facebook user access_token and return the user's profile.
    Returns dict with: facebook_id, name (optional), email (optional).
    Raises ValueError on any verification failure.
    """
    app_access_token = f"{settings.FACEBOOK_APP_ID}|{settings.FACEBOOK_APP_SECRET}"

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1: Inspect the token to confirm it belongs to our app
        debug_resp = await client.get(
            f"{GRAPH_URL}/debug_token",
            params={"input_token": access_token, "access_token": app_access_token},
        )
        debug_resp.raise_for_status()
        debug_data = debug_resp.json().get("data", {})

        if not debug_data.get("is_valid"):
            raise ValueError("Facebook access token is not valid")

        if str(debug_data.get("app_id")) != str(settings.FACEBOOK_APP_ID):
            raise ValueError("Facebook token was not issued for this app")

        # Step 2: Fetch user profile
        me_resp = await client.get(
            f"{GRAPH_URL}/me",
            params={"fields": "id,name,email", "access_token": access_token},
        )
        me_resp.raise_for_status()
        me_data = me_resp.json()

    facebook_id: str | None = me_data.get("id")
    if not facebook_id:
        raise ValueError("Could not retrieve Facebook user ID")

    return {
        "facebook_id": facebook_id,
        "name": me_data.get("name"),
        "email": me_data.get("email"),
    }
