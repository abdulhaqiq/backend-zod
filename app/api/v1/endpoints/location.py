"""
Location endpoints — all geocoding is done on-device (Apple CoreLocation / expo-location).
The backend stores whatever the client sends; no server-side geocoding needed.

POST /location/update
  Accepts lat/lng + Apple-geocoded city/address/country from the device.

POST /location/change-city  (Pro only)
  Travel mode: client sends city/country + Apple-geocoded lat/lon.

GET  /location/city-search
  Returns a curated static list filtered by query (no external API needed).
"""
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_pro_user
from app.db.session import get_db
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/location", tags=["location"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class LocationUpdateRequest(BaseModel):
    latitude: float
    longitude: float
    # Apple CoreLocation / expo-location supplies these on-device
    city:    str | None = None
    address: str | None = None
    country: str | None = None


class LocationResponse(BaseModel):
    city:                str | None
    address:             str | None
    country:             str | None
    location_updated_at: datetime | None


class ChangeCityRequest(BaseModel):
    city:      str
    country:   str
    # Client geocodes the city with Apple Maps and sends the coords
    latitude:  float | None = None
    longitude: float | None = None


# ─── POST /location/update ────────────────────────────────────────────────────

@router.post("/update", response_model=LocationResponse, summary="Update user's current location")
async def update_location(
    body: LocationUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called on app open when location permission is granted.
    The client does all geocoding via Apple CoreLocation (expo-location.reverseGeocodeAsync).
    """
    if current_user.travel_mode_enabled:
        return {
            "city":                current_user.city,
            "address":             current_user.address,
            "country":             current_user.country,
            "location_updated_at": current_user.location_updated_at,
        }

    current_user.latitude            = body.latitude
    current_user.longitude           = body.longitude
    current_user.city                = body.city
    current_user.address             = body.address
    current_user.country             = body.country
    current_user.location_updated_at = datetime.now(timezone.utc)

    await db.commit()

    logger.info(
        "Location updated for user %s → (%.4f, %.4f) %s, %s",
        current_user.id, body.latitude, body.longitude, body.city, body.country,
    )

    return {
        "city":                body.city,
        "address":             body.address,
        "country":             body.country,
        "location_updated_at": current_user.location_updated_at,
    }


# ─── POST /location/change-city ───────────────────────────────────────────────

@router.post("/change-city", summary="Set travel / discovery location — Pro only")
async def change_city(
    body: ChangeCityRequest,
    current_user: User = Depends(get_pro_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Travel mode: client geocodes the city via Apple Maps and sends the resolved coordinates.
    If no coords provided the user's last known lat/lon is preserved.
    """
    # Snapshot real GPS before overwriting (only on first activation)
    if not current_user.travel_mode_enabled:
        current_user.real_latitude  = current_user.latitude
        current_user.real_longitude = current_user.longitude
        current_user.real_city      = current_user.city
        current_user.real_country   = current_user.country

    now = datetime.now(timezone.utc)
    current_user.city                = body.city
    current_user.country             = body.country
    current_user.travel_mode_enabled = True
    current_user.travel_city         = body.city
    current_user.travel_country      = body.country
    current_user.travel_expires_at   = now + timedelta(days=7)

    if body.latitude is not None and body.longitude is not None:
        current_user.latitude            = body.latitude
        current_user.longitude           = body.longitude
        current_user.location_updated_at = now

    await db.commit()

    logger.info(
        "Travel mode set for user %s → %s, %s (coords=%s/%s)",
        current_user.id, body.city, body.country, body.latitude, body.longitude,
    )

    return {
        "city":                body.city,
        "country":             body.country,
        "travel_mode_enabled": True,
        "travel_expires_at":   current_user.travel_expires_at.isoformat(),
    }


