"""
Location endpoints — auto-update user's current location on app open.

POST /location/update
  Accepts lat/lng from the device.
  If GOOGLE_MAPS_API_KEY is set, reverse-geocodes server-side via Google Maps API.
  Falls back to storing raw coords + any client-supplied address fields.
  Saves: latitude, longitude, city, address, country, location_updated_at.
"""
import logging
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_pro_user
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
    # Coordinates are intentionally omitted — never sent back to the client
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
    Skipped when the user has travel mode enabled — the manually chosen city is
    preserved until they explicitly change or disable it.
    """
    # Guard: never overwrite a manually set travel location with real GPS
    if current_user.travel_mode_enabled:
        return {
            "city":    current_user.city,
            "address": current_user.address,
            "country": current_user.country,
            "location_updated_at": current_user.location_updated_at,
        }

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
        "city": city,
        "address": address,
        "country": country,
        "location_updated_at": current_user.location_updated_at,
    }


# ─── Geocode a place_id / city name to lat/lon ────────────────────────────────

async def _geocode_place(place_id: str | None, city: str, country: str) -> tuple[float, float] | None:
    """
    Returns (latitude, longitude) for the given place_id or city+country string.
    Uses Google Geocoding API when GOOGLE_MAPS_API_KEY is set, otherwise None.
    """
    key = settings.GOOGLE_MAPS_API_KEY
    if not key:
        return None

    # Prefer place_id geocoding — most accurate
    if place_id:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?place_id={place_id}&key={key}"
    else:
        q = f"{city}, {country}"
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={q}&key={key}"

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
        data = r.json()
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])
    except Exception as exc:
        logger.warning("Geocode failed for place_id=%s city=%s: %s", place_id, city, exc)
    return None


class ChangeCityRequest(BaseModel):
    city: str
    country: str
    place_id: str | None = None


@router.post("/change-city", summary="Manually set discovery location (travel mode) — Pro only")
async def change_city(
    body: ChangeCityRequest,
    current_user: User = Depends(get_pro_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Updates the user's discovery latitude/longitude to the selected city.
    Also sets city, country, travel_mode_enabled=True, travel_city, travel_country.
    This is exactly how Bumble's travel mode works — the feed will now show
    profiles from the new city.
    """
    coords = await _geocode_place(body.place_id, body.city, body.country)

    # Snapshot real GPS before overwriting (only on the first activation, not if already in travel mode)
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

    if coords:
        current_user.latitude  = coords[0]
        current_user.longitude = coords[1]
        current_user.location_updated_at = now

    await db.commit()

    logger.info(
        "Travel mode set for user %s → %s, %s (coords=%s, expires=%s)",
        current_user.id, body.city, body.country, coords, current_user.travel_expires_at,
    )

    return {
        "city":               body.city,
        "country":            body.country,
        "travel_mode_enabled": True,
        "travel_expires_at":  current_user.travel_expires_at.isoformat(),
    }


# ─── City search (Places Autocomplete) ───────────────────────────────────────

# country_code → flag emoji  (ISO 3166-1 alpha-2)
def _flag(code: str) -> str:
    code = (code or "").upper().strip()
    if len(code) != 2:
        return "🌍"
    return chr(0x1F1E6 + ord(code[0]) - ord('A')) + chr(0x1F1E6 + ord(code[1]) - ord('A'))


@router.get("/city-search", summary="Search cities via Google Places Autocomplete")
async def city_search(
    q: str = "",
    current_user: User = Depends(get_current_user),
):
    """
    Returns up to 8 matching cities with country name and flag emoji.
    When q is empty, returns a curated list of top world cities.
    Uses Google Places Autocomplete (types=cities) when GOOGLE_MAPS_API_KEY is set.
    Falls back to a curated static list for offline/dev use.
    """
    q = q.strip()

    # ── Static list (always available) ────────────────────────────────────────
    static = [
        {"city": "Abu Dhabi",       "country": "UAE",              "flag": "🇦🇪"},
        {"city": "Accra",           "country": "Ghana",            "flag": "🇬🇭"},
        {"city": "Amsterdam",       "country": "Netherlands",      "flag": "🇳🇱"},
        {"city": "Atlanta",         "country": "United States",    "flag": "🇺🇸"},
        {"city": "Auckland",        "country": "New Zealand",      "flag": "🇳🇿"},
        {"city": "Bangkok",         "country": "Thailand",         "flag": "🇹🇭"},
        {"city": "Barcelona",       "country": "Spain",            "flag": "🇪🇸"},
        {"city": "Beijing",         "country": "China",            "flag": "🇨🇳"},
        {"city": "Berlin",          "country": "Germany",          "flag": "🇩🇪"},
        {"city": "Boston",          "country": "United States",    "flag": "🇺🇸"},
        {"city": "Brussels",        "country": "Belgium",          "flag": "🇧🇪"},
        {"city": "Buenos Aires",    "country": "Argentina",        "flag": "🇦🇷"},
        {"city": "Cairo",           "country": "Egypt",            "flag": "🇪🇬"},
        {"city": "Cape Town",       "country": "South Africa",     "flag": "🇿🇦"},
        {"city": "Chicago",         "country": "United States",    "flag": "🇺🇸"},
        {"city": "Copenhagen",      "country": "Denmark",          "flag": "🇩🇰"},
        {"city": "Dallas",          "country": "United States",    "flag": "🇺🇸"},
        {"city": "Delhi",           "country": "India",            "flag": "🇮🇳"},
        {"city": "Doha",            "country": "Qatar",            "flag": "🇶🇦"},
        {"city": "Dubai",           "country": "UAE",              "flag": "🇦🇪"},
        {"city": "Dublin",          "country": "Ireland",          "flag": "🇮🇪"},
        {"city": "Frankfurt",       "country": "Germany",          "flag": "🇩🇪"},
        {"city": "Geneva",          "country": "Switzerland",      "flag": "🇨🇭"},
        {"city": "Hong Kong",       "country": "Hong Kong",        "flag": "🇭🇰"},
        {"city": "Houston",         "country": "United States",    "flag": "🇺🇸"},
        {"city": "Istanbul",        "country": "Turkey",           "flag": "🇹🇷"},
        {"city": "Jakarta",         "country": "Indonesia",        "flag": "🇮🇩"},
        {"city": "Johannesburg",    "country": "South Africa",     "flag": "🇿🇦"},
        {"city": "Karachi",         "country": "Pakistan",         "flag": "🇵🇰"},
        {"city": "Kuala Lumpur",    "country": "Malaysia",         "flag": "🇲🇾"},
        {"city": "Lagos",           "country": "Nigeria",          "flag": "🇳🇬"},
        {"city": "Lahore",          "country": "Pakistan",         "flag": "🇵🇰"},
        {"city": "Lima",            "country": "Peru",             "flag": "🇵🇪"},
        {"city": "Lisbon",          "country": "Portugal",         "flag": "🇵🇹"},
        {"city": "London",          "country": "United Kingdom",   "flag": "🇬🇧"},
        {"city": "Los Angeles",     "country": "United States",    "flag": "🇺🇸"},
        {"city": "Madrid",          "country": "Spain",            "flag": "🇪🇸"},
        {"city": "Manchester",      "country": "United Kingdom",   "flag": "🇬🇧"},
        {"city": "Manila",          "country": "Philippines",      "flag": "🇵🇭"},
        {"city": "Melbourne",       "country": "Australia",        "flag": "🇦🇺"},
        {"city": "Mexico City",     "country": "Mexico",           "flag": "🇲🇽"},
        {"city": "Miami",           "country": "United States",    "flag": "🇺🇸"},
        {"city": "Milan",           "country": "Italy",            "flag": "🇮🇹"},
        {"city": "Montreal",        "country": "Canada",           "flag": "🇨🇦"},
        {"city": "Moscow",          "country": "Russia",           "flag": "🇷🇺"},
        {"city": "Mumbai",          "country": "India",            "flag": "🇮🇳"},
        {"city": "Munich",          "country": "Germany",          "flag": "🇩🇪"},
        {"city": "Nairobi",         "country": "Kenya",            "flag": "🇰🇪"},
        {"city": "New York",        "country": "United States",    "flag": "🇺🇸"},
        {"city": "Osaka",           "country": "Japan",            "flag": "🇯🇵"},
        {"city": "Oslo",            "country": "Norway",           "flag": "🇳🇴"},
        {"city": "Paris",           "country": "France",           "flag": "🇫🇷"},
        {"city": "Riyadh",          "country": "Saudi Arabia",     "flag": "🇸🇦"},
        {"city": "Rome",            "country": "Italy",            "flag": "🇮🇹"},
        {"city": "San Francisco",   "country": "United States",    "flag": "🇺🇸"},
        {"city": "Santiago",        "country": "Chile",            "flag": "🇨🇱"},
        {"city": "São Paulo",       "country": "Brazil",           "flag": "🇧🇷"},
        {"city": "Seoul",           "country": "South Korea",      "flag": "🇰🇷"},
        {"city": "Shanghai",        "country": "China",            "flag": "🇨🇳"},
        {"city": "Singapore",       "country": "Singapore",        "flag": "🇸🇬"},
        {"city": "Stockholm",       "country": "Sweden",           "flag": "🇸🇪"},
        {"city": "Sydney",          "country": "Australia",        "flag": "🇦🇺"},
        {"city": "Taipei",          "country": "Taiwan",           "flag": "🇹🇼"},
        {"city": "Tehran",          "country": "Iran",             "flag": "🇮🇷"},
        {"city": "Tel Aviv",        "country": "Israel",           "flag": "🇮🇱"},
        {"city": "Tokyo",           "country": "Japan",            "flag": "🇯🇵"},
        {"city": "Toronto",         "country": "Canada",           "flag": "🇨🇦"},
        {"city": "Vancouver",       "country": "Canada",           "flag": "🇨🇦"},
        {"city": "Vienna",          "country": "Austria",          "flag": "🇦🇹"},
        {"city": "Warsaw",          "country": "Poland",           "flag": "🇵🇱"},
        {"city": "Zurich",          "country": "Switzerland",      "flag": "🇨🇭"},
    ]

    # ── Empty query → return popular cities ───────────────────────────────────
    if not q:
        popular = [
            "London", "New York", "Dubai", "Paris", "Tokyo",
            "Los Angeles", "Singapore", "Sydney", "Toronto", "Berlin",
            "Amsterdam", "Barcelona", "Istanbul", "Bangkok", "Mumbai",
        ]
        top = [c for c in static if c["city"] in popular]
        # Sort by the popular order
        order = {city: i for i, city in enumerate(popular)}
        top.sort(key=lambda c: order.get(c["city"], 99))
        return {"results": top}

    # ── Google Places Autocomplete ─────────────────────────────────────────────
    key = settings.GOOGLE_MAPS_API_KEY
    if key:
        url = (
            "https://maps.googleapis.com/maps/api/place/autocomplete/json"
            f"?input={q}&types=(cities)&key={key}&language=en"
        )
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url)
            data = r.json()
            if data.get("status") == "OK":
                results = []
                seen = set()
                for pred in data.get("predictions", [])[:8]:
                    terms = pred.get("terms", [])
                    city    = terms[0]["value"] if len(terms) >= 1 else pred["description"]
                    # Use full description for country to avoid "USA" collisions
                    # e.g. "London, Kentucky, United States" → country="United States"
                    country = terms[-1]["value"] if len(terms) >= 2 else ""
                    # If only 2 terms (city, country) that's fine; if 3+ use last
                    # For "London, Ontario, Canada" → terms = [London, Ontario, Canada]
                    if len(terms) >= 3:
                        country = terms[-1]["value"]
                    place_id = pred.get("place_id", "")
                    flag = "🌍"
                    if place_id:
                        try:
                            det_url = (
                                "https://maps.googleapis.com/maps/api/place/details/json"
                                f"?place_id={place_id}&fields=address_components&key={key}"
                            )
                            async with httpx.AsyncClient(timeout=3) as dc:
                                dr = await dc.get(det_url)
                            det = dr.json()
                            for comp in det.get("result", {}).get("address_components", []):
                                if "country" in comp.get("types", []):
                                    flag = _flag(comp.get("short_name", ""))
                                    # Use full country name from geocode details
                                    country = comp.get("long_name", country)
                                    break
                        except Exception:
                            pass
                    # Use full description as dedup key to handle same-name cities
                    dedup_key = pred.get("description", f"{city}-{country}")
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    results.append({"city": city, "country": country, "flag": flag, "place_id": place_id})
                return {"results": results}
        except Exception as exc:
            logger.warning("Places Autocomplete failed: %s", exc)

    # ── Static fallback ────────────────────────────────────────────────────────
    ql = q.lower()
    matches = [c for c in static if ql in c["city"].lower() or ql in c["country"].lower()]
    return {"results": matches[:8]}
