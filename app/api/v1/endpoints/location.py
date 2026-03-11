"""
Location endpoints — auto-update user's current location on app open.

POST /location/update
  Accepts lat/lng from the device.
  If GOOGLE_MAPS_API_KEY is set, reverse-geocodes server-side via Google Maps API.
  Falls back to storing raw coords + any client-supplied address fields.
  Saves: latitude, longitude, city, address, country, location_updated_at.
"""
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/location", tags=["location"])


# ─── Schema ───────────────────────────────────────────────────────────────────

class LocationUpdateRequest(BaseModel):
    latitude: float
    longitude: float
    # Optional: client can pre-fill these from device geocoder as fallback
    city: str | None = None
    address: str | None = None
    country: str | None = None


class LocationResponse(BaseModel):
    latitude: float
    longitude: float
    city: str | None
    address: str | None
    country: str | None
    location_updated_at: datetime | None


# ─── Google Maps reverse geocoder ─────────────────────────────────────────────

async def _google_reverse_geocode(lat: float, lng: float) -> dict:
    """
    Calls Google Maps Geocoding API and returns {city, address, country}.
    Returns empty dict if key not set or request fails.
    """
    key = settings.GOOGLE_MAPS_API_KEY
    if not key:
        return {}

    url = (
        f"https://maps.googleapis.com/maps/api/geocode/json"
        f"?latlng={lat},{lng}&key={key}"
    )
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
        data = r.json()
    except Exception as exc:
        logger.warning("Google Maps geocode failed: %s", exc)
        return {}

    if data.get("status") != "OK" or not data.get("results"):
        logger.warning("Google Maps geocode bad status: %s", data.get("status"))
        return {}

    result = data["results"][0]
    formatted_address = result.get("formatted_address", "")

    # Extract city and country from address_components
    city = country = None
    for comp in result.get("address_components", []):
        types = comp.get("types", [])
        if "locality" in types:
            city = comp.get("long_name")
        elif "administrative_area_level_1" in types and not city:
            city = comp.get("long_name")
        if "country" in types:
            country = comp.get("long_name")

    return {"city": city, "address": formatted_address, "country": country}


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@router.post("/update", response_model=LocationResponse, summary="Update user's current location")
async def update_location(
    body: LocationUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called automatically every time the app opens (if location permission granted).
    Stores GPS coordinates and reverse-geocoded address data.
    """
    # Try Google Maps first; fall back to client-supplied values
    geo = await _google_reverse_geocode(body.latitude, body.longitude)

    city    = geo.get("city")    or body.city
    address = geo.get("address") or body.address
    country = geo.get("country") or body.country

    current_user.latitude           = body.latitude
    current_user.longitude          = body.longitude
    current_user.city               = city
    current_user.address            = address
    current_user.country            = country
    current_user.location_updated_at = datetime.now(timezone.utc)

    await db.commit()

    logger.info(
        "Location updated for user %s → (%.4f, %.4f) %s, %s",
        current_user.id, body.latitude, body.longitude, city, country,
    )

    return {
        "latitude": body.latitude,
        "longitude": body.longitude,
        "city": city,
        "address": address,
        "country": country,
        "location_updated_at": current_user.location_updated_at,
    }
