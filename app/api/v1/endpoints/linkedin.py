"""
LinkedIn OAuth import endpoints + profile scraper.

OAuth Flow
──────────
1. Frontend opens LinkedIn OAuth in an in-app browser.
2. LinkedIn redirects to LINKEDIN_REDIRECT_URI with ?code=<auth_code>.
3. App extracts the code and POSTs it to /api/v1/linkedin/import-work or
   /api/v1/linkedin/import-education.
4. Backend exchanges the code for an access token, calls the LinkedIn API,
   and returns structured data ready to save to the user profile.

Scraper endpoints
─────────────────
POST /linkedin/enrich  – enrich the authenticated user's profile from their
                         stored linkedin_url using the multi-tier scraper.
POST /linkedin/scrape  – admin-only: scrape any LinkedIn URL and return raw
                         structured data (does not write to the DB).

LinkedIn API notes
──────────────────
- Basic scopes (openid profile email / r_liteprofile r_emailaddress): give
  name, headline, email. Headline is typically "Job Title at Company".
- r_fullprofile (requires LinkedIn Marketing Developer Program approval):
  gives /v2/positions and /v2/educations endpoints.
- We try the full-access endpoints first, then fall back to headline parsing
  so the feature works for non-partner apps too.
"""

import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services.linkedin_scraper import LinkedInScraperService, ScrapedLinkedInProfile

# Monthly import limits per subscription tier (None = unlimited)
_MONTHLY_LIMITS: dict[str, int | None] = {
    "free": 1,
    "pro": 2,
    "premium_plus": None,
}

router = APIRouter(prefix="/linkedin", tags=["linkedin"])

# ─── OAuth callback ────────────────────────────────────────────────────────────

@router.get("/callback")
async def linkedin_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """
    LinkedIn redirects here after the user authorises the app.

    We immediately redirect back into the mobile app via the 'zod://' deep-link
    scheme so that expo-web-browser / ASWebAuthenticationSession can intercept
    it and hand the auth code back to the JavaScript layer.

    Register https://dev.zod.ailoo.co/api/v1/linkedin/callback
    as an Authorised Redirect URL in your LinkedIn Developer Portal.
    """
    if error or not code:
        desc = error_description or error or "access_denied"
        return RedirectResponse(f"zod://linkedin?error={desc}")
    state_part = f"&state={state}" if state else ""
    return RedirectResponse(f"zod://linkedin?code={code}{state_part}")


# ─── Shared helpers ────────────────────────────────────────────────────────────

_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def _check_credentials() -> None:
    if not settings.LINKEDIN_CLIENT_ID or not settings.LINKEDIN_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "LinkedIn import is not configured. "
                "Ask the app admin to set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET."
            ),
        )


async def _exchange_code(client: httpx.AsyncClient, code: str, redirect_uri: str) -> str:
    """Exchange an OAuth authorization code for a LinkedIn access token."""
    res = await client.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if res.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"LinkedIn token exchange failed: {res.text}",
        )
    access_token = res.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token returned by LinkedIn.")
    return access_token


def _parse_headline(headline: str) -> tuple[str, str]:
    """
    Parse a LinkedIn headline into (job_title, company).
    e.g. "Senior Engineer at Google" -> ("Senior Engineer", "Google")
         "PM · Stripe | ex-Meta"     -> ("PM", "Stripe")
    """
    for sep in (" at ", " @ ", " · ", " | ", " - "):
        if sep in headline:
            parts = headline.split(sep, 1)
            return parts[0].strip(), parts[1].strip().split("|")[0].split("·")[0].strip()
    return headline.strip(), ""


# ─── Request/Response models ────────────────────────────────────────────────────

class LinkedInCodeRequest(BaseModel):
    code: str
    redirect_uri: str


class WorkEntry(BaseModel):
    job_title: str
    company: str
    start_year: str
    end_year: str
    current: bool


class EduEntry(BaseModel):
    institution: str
    course: str
    degree: str
    grad_year: str


class LinkedInVerifyResponse(BaseModel):
    linkedin_verified: bool
    linkedin_url: str | None


# ─── Account verification ────────────────────────────────────────────────────

@router.post("/verify", response_model=LinkedInVerifyResponse)
async def verify_linkedin(
    body: LinkedInCodeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify the user's identity via LinkedIn OAuth.

    Exchanges the auth code for an access token, retrieves the LinkedIn member
    ID (sub) and attempts to get the vanity profile URL, then stores
    linkedin_id, linkedin_url, and linkedin_verified=True on the user record.
    """
    _check_credentials()

    async with httpx.AsyncClient(timeout=20) as client:
        access_token = await _exchange_code(client, body.code, body.redirect_uri)

        # OIDC userInfo gives us the stable member ID (sub)
        userinfo_res = await client.get(
            "https://api.linkedin.com/v2/userInfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to retrieve LinkedIn profile.")

        userinfo = userinfo_res.json()
        linkedin_id: str = userinfo.get("sub", "")

        # Try to get vanityName for a proper profile URL
        linkedin_url: str | None = None
        me_res = await client.get(
            "https://api.linkedin.com/v2/me?projection=(id,vanityName)",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if me_res.status_code == 200:
            vanity = me_res.json().get("vanityName")
            if vanity:
                linkedin_url = f"https://www.linkedin.com/in/{vanity}"

    # Check for duplicate LinkedIn URL (other users already using this profile)
    if linkedin_url:
        from sqlalchemy import select
        stmt = select(User).where(
            User.linkedin_url == linkedin_url,
            User.id != current_user.id
        )
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            raise HTTPException(
                status_code=409,
                detail="This LinkedIn profile is already connected to another account.",
            )

    # Persist to DB
    current_user.linkedin_id = linkedin_id
    current_user.linkedin_url = linkedin_url
    current_user.linkedin_verified = True
    db.add(current_user)
    await db.commit()

    return LinkedInVerifyResponse(linkedin_verified=True, linkedin_url=linkedin_url)


# ─── Work experience import ─────────────────────────────────────────────────────

@router.post("/import-work", response_model=list[WorkEntry])
async def import_linkedin_work(
    body: LinkedInCodeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Import the user's LinkedIn work experience.

    Tries the full LinkedIn Positions API first (requires r_fullprofile scope,
    available to LinkedIn Marketing Developer Program partners). Falls back to
    parsing the user's profile headline for a single entry if the full API is
    not accessible.
    """
    _check_credentials()

    async with httpx.AsyncClient(timeout=20) as client:
        access_token = await _exchange_code(client, body.code, body.redirect_uri)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": "202304",
        }

        # ── Attempt 1: r_fullprofile Positions API ──────────────────────────
        positions_res = await client.get(
            "https://api.linkedin.com/v2/positions?q=members&count=10"
            "&projection=(elements*(id,title,companyName,timePeriod))",
            headers=headers,
        )

        if positions_res.status_code == 200:
            entries: list[WorkEntry] = []
            for pos in positions_res.json().get("elements", []):
                title_loc = pos.get("title", {}).get("localized", {})
                title_str = next(iter(title_loc.values()), "") if title_loc else ""

                company_loc = pos.get("companyName", {}).get("localized", {})
                company_str = next(iter(company_loc.values()), "") if company_loc else ""

                period = pos.get("timePeriod", {})
                start_date = period.get("startDate", {})
                end_date = period.get("endDate")

                start_year = str(start_date.get("year", "")) if start_date else ""
                is_current = end_date is None
                end_year = "" if is_current else str(end_date.get("year", ""))

                if title_str or company_str:
                    entries.append(WorkEntry(
                        job_title=title_str,
                        company=company_str,
                        start_year=start_year,
                        end_year=end_year,
                        current=is_current,
                    ))
            if entries:
                return entries

        # ── Attempt 2: headline from /v2/userInfo (OIDC) ───────────────────
        userinfo_res = await client.get(
            "https://api.linkedin.com/v2/userInfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        headline = ""
        if userinfo_res.status_code == 200:
            ui = userinfo_res.json()
            # `headline` is a LinkedIn extension; standard OIDC doesn't include it
            headline = ui.get("headline", "") or ui.get("localizedHeadline", "") or ""

        # ── Attempt 3: /v2/me projection (r_liteprofile legacy scope) ──────
        if not headline:
            me_res = await client.get(
                "https://api.linkedin.com/v2/me"
                "?projection=(id,localizedFirstName,localizedLastName,"
                "headline,localizedHeadline)",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_res.status_code == 200:
                me = me_res.json()
                headline = (
                    me.get("localizedHeadline", "")
                    or me.get("headline", {}).get(
                        "localized", {}
                    ).get(
                        next(iter(me.get("headline", {}).get("localized", {})), ""), ""
                    )
                    or ""
                )

        if headline:
            job_title, company = _parse_headline(headline)
            if job_title:
                return [WorkEntry(
                    job_title=job_title,
                    company=company,
                    start_year="",
                    end_year="",
                    current=True,
                )]

    # LinkedIn connected successfully but API doesn't expose positions without
    # LinkedIn Partner Program access. Signal this to the frontend with a
    # specific error code so it can show a helpful message instead of crashing.
    raise HTTPException(
        status_code=422,
        detail="linkedin_no_data",
    )


# ─── Education import ───────────────────────────────────────────────────────────

@router.post("/import-education", response_model=list[EduEntry])
async def import_linkedin_education(
    body: LinkedInCodeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Import the user's LinkedIn education.

    Requires the r_fullprofile scope (LinkedIn Marketing Developer Program).
    Returns an empty list gracefully if not available.
    """
    _check_credentials()

    async with httpx.AsyncClient(timeout=20) as client:
        access_token = await _exchange_code(client, body.code, body.redirect_uri)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": "202304",
        }

        edu_res = await client.get(
            "https://api.linkedin.com/v2/educations?q=members&count=10"
            "&projection=(elements*(id,schoolName,fieldOfStudy,degreeName,timePeriod))",
            headers=headers,
        )

        if edu_res.status_code != 200:
            # Education requires LinkedIn Partner Program (r_fullprofile scope).
            # Signal this clearly to the frontend.
            raise HTTPException(
                status_code=422,
                detail="linkedin_no_data",
            )

        entries: list[EduEntry] = []
        for edu in edu_res.json().get("elements", []):
            school_loc = edu.get("schoolName", {}).get("localized", {})
            institution = next(iter(school_loc.values()), "") if school_loc else ""

            field_loc = edu.get("fieldOfStudy", {}).get("localized", {})
            course = next(iter(field_loc.values()), "") if field_loc else ""

            deg_loc = edu.get("degreeName", {}).get("localized", {})
            degree = next(iter(deg_loc.values()), "") if deg_loc else ""

            period = edu.get("timePeriod", {})
            end_date = period.get("endDate", {})
            grad_year = str(end_date.get("year", "")) if end_date else ""

            if institution or course:
                entries.append(EduEntry(
                    institution=institution,
                    course=course,
                    degree=degree,
                    grad_year=grad_year,
                ))

        return entries


# ─── Scraper: enrich authenticated user's profile ────────────────────────────

class EnrichResponse(BaseModel):
    scraped: ScrapedLinkedInProfile
    updated_fields: list[str]
    imports_used: int
    imports_limit: int | None  # None = unlimited


@router.post("/enrich", response_model=EnrichResponse)
async def enrich_profile_from_linkedin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape the authenticated user's LinkedIn profile and update their DB record.

    Updated fields (always overwritten on import):
      - work_experience  ← scraped positions (with company_logo + description)
      - education        ← scraped education
      - bio              ← scraped about/summary (only if currently empty)
      - city             ← scraped city / location (only if currently empty)
      - full_name        ← scraped name (only if currently empty)

    Monthly import limits: free=1, pro=2, premium_plus=unlimited.
    """
    if not current_user.linkedin_url:
        raise HTTPException(
            status_code=400,
            detail=(
                "No LinkedIn URL on file. "
                "Enter your LinkedIn username in Work Settings and save first."
            ),
        )

    # ── Monthly usage limit check ──────────────────────────────────────────
    tier = getattr(current_user, "subscription_tier", "free") or "free"
    limit: int | None = _MONTHLY_LIMITS.get(tier, 1)

    now = datetime.now(timezone.utc)
    reset_at = getattr(current_user, "linkedin_import_reset_at", None)
    import_count = getattr(current_user, "linkedin_import_count", 0) or 0

    # Reset counter when we've rolled into a new calendar month
    if not reset_at or reset_at.month != now.month or reset_at.year != now.year:
        import_count = 0
        current_user.linkedin_import_count = 0
        current_user.linkedin_import_reset_at = now

    if limit is not None and import_count >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "import_limit_reached",
                "limit": limit,
                "tier": tier,
                "message": f"You've used all {limit} LinkedIn import(s) this month.",
            },
        )

    # ── Scrape ─────────────────────────────────────────────────────────────
    service = LinkedInScraperService()
    scraped = await service.scrape(current_user.linkedin_url)

    if scraped.error and not scraped.full_name and not scraped.positions:
        is_blocked = "999" in scraped.error or "blocked" in scraped.error.lower()
        detail = (
            "LinkedIn is blocking direct scraping. "
            "Add PROXYCURL_API_KEY or RAPIDAPI_KEY to the server .env for reliable import, "
            "or connect your LinkedIn account via OAuth."
            if is_blocked
            else f"LinkedIn scrape failed: {scraped.error}"
        )
        raise HTTPException(status_code=422, detail=detail)

    updated_fields: list[str] = []

    if scraped.full_name and not current_user.full_name:
        current_user.full_name = scraped.full_name
        updated_fields.append("full_name")

    # Always overwrite work_experience on import so logos + description stay fresh
    if scraped.positions:
        current_user.work_experience = [
            {
                "job_title":    p.title,
                "company":      p.company,
                "company_logo": p.company_logo,
                "start_year":   p.start_year,
                "end_year":     p.end_year,
                "current":      p.current,
                "description":  p.description,
            }
            for p in scraped.positions
            if p.title or p.company
        ]
        if current_user.work_experience:
            updated_fields.append("work_experience")

    # Always overwrite education on import
    if scraped.education:
        current_user.education = [
            {
                "institution": e.institution,
                "course":      e.field,
                "degree":      e.degree,
                "grad_year":   e.grad_year,
            }
            for e in scraped.education
            if e.institution or e.field
        ]
        if current_user.education:
            updated_fields.append("education")

    if scraped.about and not current_user.bio:
        current_user.bio = scraped.about[:500]
        updated_fields.append("bio")

    if not current_user.city:
        city_val = scraped.city or (scraped.location.split(",")[0].strip() if scraped.location else "")
        if city_val:
            current_user.city = city_val
            updated_fields.append("city")

    # ── Only increment counter if we actually updated something ────────────
    if not updated_fields:
        raise HTTPException(
            status_code=422,
            detail="No new data found on LinkedIn profile. Your import was not counted.",
        )
    
    current_user.linkedin_import_count = import_count + 1
    db.add(current_user)
    await db.commit()

    return EnrichResponse(
        scraped=scraped,
        updated_fields=updated_fields,
        imports_used=current_user.linkedin_import_count,
        imports_limit=limit,
    )


# ─── Scraper: admin raw-scrape any LinkedIn URL ──────────────────────────────

class ScrapeRequest(BaseModel):
    linkedin_url: str


@router.post("/scrape", response_model=ScrapedLinkedInProfile)
async def scrape_linkedin_profile(
    body: ScrapeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Admin-only: scrape any LinkedIn profile URL and return structured data.

    Does NOT modify any database records — purely returns what the scraper
    finds.  Useful for testing scrapers or manual data enrichment workflows.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")

    service = LinkedInScraperService()
    return await service.scrape(body.linkedin_url)
