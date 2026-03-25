"""
LinkedIn Profile Scraper Service
─────────────────────────────────
Multi-tier scraper with automatic fallback:

  Tier 1 – Proxycurl API        (best quality; requires PROXYCURL_API_KEY)
  Tier 2 – RapidAPI scraper     (alternative paid; requires RAPIDAPI_KEY)
  Tier 3 – Direct HTML scrape   (no key needed; best-effort, fragile)

Each tier returns a `ScrapedLinkedInProfile`.  The service tries tiers in
order and returns the first successful non-empty result.

Usage
─────
    from app.services.linkedin_scraper import LinkedInScraperService, ScrapedLinkedInProfile

    service = LinkedInScraperService()
    profile = await service.scrape("https://www.linkedin.com/in/username")
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import settings

log = logging.getLogger(__name__)


# ── Data models ──────────────────────────────────────────────────────────────


class ScrapedPosition(BaseModel):
    title: str = ""
    company: str = ""
    start_year: str = ""
    start_month: str = ""
    end_year: str = ""
    end_month: str = ""
    current: bool = False
    description: str = ""
    location: str = ""


class ScrapedEducation(BaseModel):
    institution: str = ""
    field: str = ""
    degree: str = ""
    start_year: str = ""
    grad_year: str = ""
    description: str = ""


class ScrapedLinkedInProfile(BaseModel):
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    headline: str = ""
    location: str = ""
    city: str = ""
    country: str = ""
    about: str = ""
    profile_picture: str = ""
    background_picture: str = ""
    connections: int | None = None
    follower_count: int | None = None
    positions: list[ScrapedPosition] = []
    education: list[ScrapedEducation] = []
    skills: list[str] = []
    languages: list[str] = []
    certifications: list[str] = []
    linkedin_url: str = ""
    source: str = ""       # which tier produced this result
    error: str | None = None


# ── Normalisation helpers ─────────────────────────────────────────────────────


def _clean_url(url: str) -> str:
    """Normalise a LinkedIn profile URL to https://www.linkedin.com/in/<slug>."""
    url = url.strip().rstrip("/")
    url = re.sub(r"^http://", "https://", url)
    if not url.startswith("https://"):
        url = "https://" + url
    if "linkedin.com/in/" not in url:
        raise ValueError(f"Not a LinkedIn profile URL: {url}")
    # Strip query-string / UTM params
    url = re.split(r"[?#]", url)[0]
    return url


def _year_month(date_dict: dict | None) -> tuple[str, str]:
    if not date_dict:
        return "", ""
    return str(date_dict.get("year", "")), str(date_dict.get("month", ""))


# ── Tier 1 – Proxycurl ────────────────────────────────────────────────────────


_PROXYCURL_URL = "https://nubela.co/proxycurl/api/v2/linkedin"


async def _scrape_proxycurl(url: str) -> ScrapedLinkedInProfile:
    """
    Fetch a LinkedIn profile via the Proxycurl API.
    https://nubela.co/proxycurl/docs#people-api-linkedin-profile-endpoint
    """
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            _PROXYCURL_URL,
            headers={"Authorization": f"Bearer {settings.PROXYCURL_API_KEY}"},
            params={
                "url": url,
                "skills": "include",
                "inferred_salary": "skip",
                "personal_email": "skip",
                "personal_contact_number": "skip",
                "twitter_profile_id": "skip",
                "facebook_profile_id": "skip",
                "github_profile_id": "skip",
                "extra": "include",
                "use_cache": "if-recent",
                "fallback_to_cache": "on-error",
            },
        )

    if res.status_code == 404:
        raise ValueError("LinkedIn profile not found (Proxycurl 404).")
    if res.status_code == 401:
        raise PermissionError("Invalid Proxycurl API key.")
    if res.status_code == 402:
        raise PermissionError("Proxycurl account has insufficient credits.")
    if res.status_code != 200:
        raise RuntimeError(f"Proxycurl returned HTTP {res.status_code}: {res.text[:200]}")

    d: dict[str, Any] = res.json()

    positions: list[ScrapedPosition] = []
    for exp in d.get("experiences") or []:
        sy, sm = _year_month(exp.get("starts_at"))
        ey, em = _year_month(exp.get("ends_at"))
        positions.append(ScrapedPosition(
            title=exp.get("title") or "",
            company=exp.get("company") or "",
            start_year=sy,
            start_month=sm,
            end_year=ey,
            end_month=em,
            current=exp.get("ends_at") is None,
            description=exp.get("description") or "",
            location=exp.get("location") or "",
        ))

    education: list[ScrapedEducation] = []
    for edu in d.get("education") or []:
        sy, _ = _year_month(edu.get("starts_at"))
        gy, _ = _year_month(edu.get("ends_at"))
        education.append(ScrapedEducation(
            institution=edu.get("school") or "",
            field=edu.get("field_of_study") or "",
            degree=edu.get("degree_name") or "",
            start_year=sy,
            grad_year=gy,
            description=edu.get("description") or "",
        ))

    skills = [s.get("name", "") for s in (d.get("skills") or []) if s.get("name")]
    langs = [ln.get("name", "") for ln in (d.get("languages") or []) if ln.get("name")]
    certs = [
        c.get("name", "")
        for c in (d.get("certifications") or [])
        if c.get("name")
    ]

    location_str = ", ".join(filter(None, [d.get("city"), d.get("country_full_name")]))

    return ScrapedLinkedInProfile(
        full_name=d.get("full_name") or "",
        first_name=d.get("first_name") or "",
        last_name=d.get("last_name") or "",
        headline=d.get("headline") or "",
        location=location_str,
        city=d.get("city") or "",
        country=d.get("country_full_name") or "",
        about=d.get("summary") or "",
        profile_picture=d.get("profile_pic_url") or "",
        background_picture=d.get("background_cover_image_url") or "",
        connections=d.get("connections"),
        follower_count=d.get("follower_count"),
        positions=positions,
        education=education,
        skills=skills,
        languages=langs,
        certifications=certs,
        linkedin_url=url,
        source="proxycurl",
    )


# ── Tier 2 – RapidAPI (Fresh LinkedIn Profile Data) ──────────────────────────


_RAPIDAPI_HOST = "fresh-linkedin-profile-data.p.rapidapi.com"
_RAPIDAPI_URL = "https://fresh-linkedin-profile-data.p.rapidapi.com/get-linkedin-profile"


async def _scrape_rapidapi(url: str) -> ScrapedLinkedInProfile:
    """
    Fetch a LinkedIn profile via the RapidAPI 'Fresh LinkedIn Profile Data' endpoint.
    https://rapidapi.com/freshdata-freshdata-default/api/fresh-linkedin-profile-data
    """
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            _RAPIDAPI_URL,
            headers={
                "x-rapidapi-host": _RAPIDAPI_HOST,
                "x-rapidapi-key": settings.RAPIDAPI_KEY,
            },
            params={"linkedin_url": url, "include_skills": "true"},
        )

    if res.status_code == 403:
        raise PermissionError("Invalid RapidAPI key or not subscribed to the LinkedIn endpoint.")
    if res.status_code != 200:
        raise RuntimeError(f"RapidAPI returned HTTP {res.status_code}: {res.text[:200]}")

    d: dict[str, Any] = res.json().get("data", res.json())

    positions: list[ScrapedPosition] = []
    for exp in d.get("experience") or []:
        # Fresh LinkedIn API uses string date fields like "Jan 2020"
        def _parse_year(raw: str | None) -> str:
            if not raw:
                return ""
            m = re.search(r"\b(19|20)\d{2}\b", raw)
            return m.group(0) if m else ""

        positions.append(ScrapedPosition(
            title=exp.get("title") or "",
            company=exp.get("company") or exp.get("company_name") or "",
            start_year=_parse_year(exp.get("start_date") or exp.get("date_range", "").split("–")[0]),
            end_year=_parse_year(exp.get("end_date") or (exp.get("date_range", "–").split("–") + [""])[1]),
            current="present" in (exp.get("end_date") or exp.get("date_range") or "").lower(),
            description=exp.get("description") or "",
        ))

    education: list[ScrapedEducation] = []
    for edu in d.get("education") or []:
        def _parse_year(raw: str | None) -> str:
            if not raw:
                return ""
            m = re.search(r"\b(19|20)\d{2}\b", raw)
            return m.group(0) if m else ""

        education.append(ScrapedEducation(
            institution=edu.get("school") or edu.get("institution") or "",
            field=edu.get("field_of_study") or edu.get("field") or "",
            degree=edu.get("degree") or edu.get("degree_name") or "",
            start_year=_parse_year(edu.get("start_date")),
            grad_year=_parse_year(edu.get("end_date")),
        ))

    skills = [s if isinstance(s, str) else s.get("name", "") for s in (d.get("skills") or [])]
    skills = [s for s in skills if s]

    return ScrapedLinkedInProfile(
        full_name=d.get("full_name") or f"{d.get('first_name','')} {d.get('last_name','')}".strip(),
        first_name=d.get("first_name") or "",
        last_name=d.get("last_name") or "",
        headline=d.get("headline") or d.get("title") or "",
        location=d.get("location") or "",
        city=d.get("city") or "",
        country=d.get("country") or "",
        about=d.get("about") or d.get("summary") or "",
        profile_picture=d.get("profile_picture") or d.get("photo") or "",
        positions=positions,
        education=education,
        skills=skills,
        linkedin_url=url,
        source="rapidapi",
    )


# ── Tier 3 – Direct HTML scraping ─────────────────────────────────────────────


_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _parse_html_profile(html: str, url: str) -> ScrapedLinkedInProfile:
    """
    Best-effort extraction from LinkedIn public-profile HTML.

    LinkedIn renders most content client-side (React), but public profiles do
    embed some server-side data in:
      1.  <script type="application/ld+json">   – schema.org Person graph
      2.  Open Graph <meta> tags                – name, title, description, image
      3.  <code id="*"> data blobs              – partially rendered JSON

    We try all three and merge results.
    """
    full_name = headline = about = image = location = ""
    positions: list[ScrapedPosition] = []
    education: list[ScrapedEducation] = []
    skills: list[str] = []

    # ── 1. JSON-LD schema.org ────────────────────────────────────────────────
    for blob in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            ld = json.loads(blob)
        except json.JSONDecodeError:
            continue

        graphs = ld if isinstance(ld, list) else [ld]
        for node in graphs:
            if isinstance(node, dict) and node.get("@type") in ("Person", "ProfilePage"):
                person = node if node.get("@type") == "Person" else node.get("mainEntity", {})
                full_name = full_name or person.get("name", "")
                headline = headline or person.get("jobTitle", "")
                about = about or person.get("description", "")
                image_obj = person.get("image", {})
                if isinstance(image_obj, str):
                    image = image or image_obj
                elif isinstance(image_obj, dict):
                    image = image or image_obj.get("contentUrl", "") or image_obj.get("url", "")

                # worksFor → positions
                for employer in person.get("worksFor", []):
                    if isinstance(employer, dict):
                        company = employer.get("name", "")
                        if company:
                            positions.append(ScrapedPosition(
                                company=company,
                                title=headline,
                                current=True,
                            ))

                # alumniOf → education
                for school in person.get("alumniOf", []):
                    if isinstance(school, str):
                        education.append(ScrapedEducation(institution=school))
                    elif isinstance(school, dict):
                        education.append(ScrapedEducation(
                            institution=school.get("name", ""),
                        ))

                # knowsAbout → skills (sometimes)
                for skill in person.get("knowsAbout", []):
                    if isinstance(skill, str) and skill:
                        skills.append(skill)

    # ── 2. Open Graph meta tags ──────────────────────────────────────────────
    def _meta(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not m:
            m = re.search(
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:{prop}["\']',
                html, re.IGNORECASE,
            )
        return m.group(1).strip() if m else ""

    og_title = _meta("title")
    og_desc = _meta("description")
    og_image = _meta("image")
    og_url = _meta("url")

    if og_title and not full_name:
        # "Full Name | LinkedIn" or "Full Name - Headline | LinkedIn"
        name_part = og_title.split("|")[0].split(" - ")[0].strip()
        full_name = name_part

    if og_title and not headline:
        # "Full Name - Headline | LinkedIn"
        if " - " in og_title.split("|")[0]:
            headline = og_title.split("|")[0].split(" - ", 1)[1].strip()

    if og_desc and not about:
        about = og_desc

    image = image or og_image

    # ── 3. Lightweight JSON data blob extraction ─────────────────────────────
    # LinkedIn embeds partial data in `<code id="...">` elements and
    # `window.__INITIAL_STATE__` / `window.prefetchedData` JS variables.
    for json_blob in re.findall(
        r'"name"\s*:\s*"([^"]{2,100})".*?"headline"\s*:\s*"([^"]{0,200})"',
        html,
        re.DOTALL,
    ):
        if not full_name and json_blob[0]:
            full_name = json_blob[0]
        if not headline and json_blob[1]:
            headline = json_blob[1]
        break

    # Extract location from common patterns
    loc_match = re.search(
        r'"geoLocationName"\s*:\s*"([^"]+)"',
        html,
    )
    if loc_match:
        location = loc_match.group(1)

    # Extract follower / connection count
    conn_match = re.search(r"(\d[\d,]+)\s+(?:connections?|followers?)", html, re.IGNORECASE)
    connections: int | None = None
    if conn_match:
        try:
            connections = int(conn_match.group(1).replace(",", ""))
        except ValueError:
            pass

    if not full_name and not headline:
        raise ValueError(
            "Could not extract any profile data from LinkedIn HTML. "
            "LinkedIn may be blocking the request or the profile is private."
        )

    return ScrapedLinkedInProfile(
        full_name=full_name,
        headline=headline,
        location=location,
        about=about,
        profile_picture=image,
        connections=connections,
        positions=positions,
        education=education,
        skills=skills,
        linkedin_url=url,
        source="direct",
    )


async def _scrape_direct(url: str) -> ScrapedLinkedInProfile:
    """Fetch and parse a LinkedIn public profile page directly."""
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers=_SCRAPE_HEADERS,
    ) as client:
        res = await client.get(url)

    if res.status_code == 404:
        raise ValueError("LinkedIn profile not found (404).")
    if res.status_code in (401, 403, 999):
        raise PermissionError(
            f"LinkedIn blocked the direct request (HTTP {res.status_code}). "
            "Set PROXYCURL_API_KEY or RAPIDAPI_KEY for reliable scraping."
        )
    if res.status_code != 200:
        raise RuntimeError(f"LinkedIn returned HTTP {res.status_code}.")

    return _parse_html_profile(res.text, url)


# ── Main service ──────────────────────────────────────────────────────────────


class LinkedInScraperService:
    """
    Multi-tier LinkedIn profile scraper.

    Tries tiers in priority order and returns the first successful result.
    Configure API keys in .env:

        PROXYCURL_API_KEY=...   # Tier 1 (https://nubela.co/proxycurl)
        RAPIDAPI_KEY=...        # Tier 2 (RapidAPI Fresh LinkedIn Profile Data)

    Tier 3 (direct HTML) is always attempted as a last resort.
    """

    async def scrape(self, linkedin_url: str) -> ScrapedLinkedInProfile:
        url = _clean_url(linkedin_url)
        errors: list[str] = []

        # Tier 1 — Proxycurl
        if settings.PROXYCURL_API_KEY:
            try:
                profile = await _scrape_proxycurl(url)
                if profile.full_name or profile.positions:
                    log.info("LinkedIn scrape via Proxycurl: %s", url)
                    return profile
            except Exception as exc:
                log.warning("Proxycurl scrape failed for %s: %s", url, exc)
                errors.append(f"proxycurl: {exc}")

        # Tier 2 — RapidAPI
        if settings.RAPIDAPI_KEY:
            try:
                profile = await _scrape_rapidapi(url)
                if profile.full_name or profile.positions:
                    log.info("LinkedIn scrape via RapidAPI: %s", url)
                    return profile
            except Exception as exc:
                log.warning("RapidAPI scrape failed for %s: %s", url, exc)
                errors.append(f"rapidapi: {exc}")

        # Tier 3 — Direct HTML
        try:
            profile = await _scrape_direct(url)
            log.info("LinkedIn scrape via direct HTML: %s", url)
            return profile
        except Exception as exc:
            log.warning("Direct HTML scrape failed for %s: %s", url, exc)
            errors.append(f"direct: {exc}")

        return ScrapedLinkedInProfile(
            linkedin_url=url,
            error="; ".join(errors) or "All scraper tiers failed.",
            source="none",
        )
