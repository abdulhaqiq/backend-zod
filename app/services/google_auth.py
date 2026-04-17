"""
Google Sign In server-side verification.

Verifies the short-lived access_token returned by the Google SDK by calling
the Google tokeninfo endpoint and fetching the user's profile via the
People API / userinfo endpoint.
"""
import httpx

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


async def verify_google_token(access_token: str) -> dict:
    """
    Verify a Google OAuth2 access_token and return the user's profile.
    Returns dict with: google_id (sub), email (optional), name (optional).
    Raises ValueError on any verification failure.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 401:
            raise ValueError("Google access token is invalid or expired")
        resp.raise_for_status()
        data = resp.json()

    google_id: str | None = data.get("sub")
    if not google_id:
        raise ValueError("Could not retrieve Google user ID")

    return {
        "google_id": google_id,
        "email": data.get("email"),
        "name": data.get("name"),
        "picture": data.get("picture"),
    }
