"""
Apple Sign In server-side verification.

Apple's identity_token is a signed JWT. We verify it by:
1. Fetching Apple's public JWKS from https://appleid.apple.com/auth/keys
2. Finding the matching key by `kid`
3. Verifying the JWT signature, issuer, audience, and expiry
"""
import logging

import httpx
from jose import JWTError, jwt

from app.core.config import settings

_log = logging.getLogger(__name__)

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


async def verify_apple_token(identity_token: str) -> dict:
    """
    Verify an Apple identity_token JWT.
    Returns the decoded payload dict with keys: apple_id, email (optional).
    Raises ValueError on any verification failure.
    """
    # Decode header without verification to get kid + alg
    try:
        unverified_header = jwt.get_unverified_header(identity_token)
    except JWTError as exc:
        raise ValueError(f"Invalid Apple token header: {exc}") from exc

    kid = unverified_header.get("kid")
    alg = unverified_header.get("alg", "RS256")

    # Peek at unverified claims to log the audience for debugging
    try:
        unverified_claims = jwt.get_unverified_claims(identity_token)
        aud = unverified_claims.get("aud")
        # Use print + ERROR so this always surfaces in the terminal
        print(f"\n>>> Apple token aud={aud!r}  expected={settings.APPLE_APP_BUNDLE_ID!r}\n")
        _log.error("Apple token aud=%r  expected=%r", aud, settings.APPLE_APP_BUNDLE_ID)
    except Exception as e:
        print(f"\n>>> Could not decode Apple token claims: {e}\n")

    # Fetch Apple public keys
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(APPLE_JWKS_URL)
            resp.raise_for_status()
            jwks = resp.json()
    except Exception as exc:
        raise ValueError(f"Failed to fetch Apple public keys: {exc}") from exc

    # Find the matching key by kid
    key_data = None
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            key_data = k
            break

    if key_data is None:
        raise ValueError(f"Apple public key not found for kid={kid!r}")

    # Verify and decode — pass the raw JWK dict directly (most compatible path)
    try:
        payload = jwt.decode(
            identity_token,
            key_data,
            algorithms=[alg],
            audience=settings.APPLE_APP_BUNDLE_ID,
            issuer=APPLE_ISSUER,
            options={"leeway": 60},  # tolerate up to 60s clock skew
        )
    except JWTError as exc:
        _log.error("Apple token verification failed: %s", exc)
        raise ValueError(f"Apple token verification failed: {exc}") from exc

    apple_id: str | None = payload.get("sub")
    if not apple_id:
        raise ValueError("Apple token missing 'sub' claim")

    return {
        "apple_id": apple_id,
        "email": payload.get("email"),
        "email_verified": payload.get("email_verified", False),
    }
