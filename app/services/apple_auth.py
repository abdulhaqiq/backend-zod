"""
Apple Sign In server-side verification.

Apple's identity_token is a signed JWT. We verify it by:
1. Fetching Apple's public JWKS from https://appleid.apple.com/auth/keys
2. Finding the matching key by `kid`
3. Verifying the JWT signature, issuer, audience, and expiry
"""
import httpx
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode

from app.core.config import settings

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


async def verify_apple_token(identity_token: str) -> dict:
    """
    Verify an Apple identity_token JWT.
    Returns the decoded payload dict with keys: sub, email (optional), email_verified.
    Raises ValueError on any verification failure.
    """
    # Fetch Apple public keys
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(APPLE_JWKS_URL)
        resp.raise_for_status()
        jwks = resp.json()

    # Get the unverified header to find the right key by kid
    try:
        unverified_header = jwt.get_unverified_header(identity_token)
    except JWTError as exc:
        raise ValueError(f"Invalid Apple token header: {exc}") from exc

    kid = unverified_header.get("kid")
    alg = unverified_header.get("alg", "RS256")

    # Find the matching public key
    public_key = None
    for key_data in jwks.get("keys", []):
        if key_data.get("kid") == kid:
            public_key = jwk.construct(key_data)
            break

    if public_key is None:
        raise ValueError("Apple public key not found for the given kid")

    # Verify and decode
    try:
        payload = jwt.decode(
            identity_token,
            public_key.to_dict(),
            algorithms=[alg],
            audience=settings.APPLE_APP_BUNDLE_ID,
            issuer=APPLE_ISSUER,
        )
    except JWTError as exc:
        raise ValueError(f"Apple token verification failed: {exc}") from exc

    apple_id: str | None = payload.get("sub")
    if not apple_id:
        raise ValueError("Apple token missing 'sub' claim")

    return {
        "apple_id": apple_id,
        "email": payload.get("email"),
        "email_verified": payload.get("email_verified", False),
    }
