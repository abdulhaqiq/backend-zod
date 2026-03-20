"""
LinkedIn OAuth import endpoints.

Flow
────
1. Frontend opens LinkedIn OAuth in an in-app browser.
2. LinkedIn redirects to LINKEDIN_REDIRECT_URI with ?code=<auth_code>.
3. App extracts the code and POSTs it to /api/v1/linkedin/import-work or
   /api/v1/linkedin/import-education.
4. Backend exchanges the code for an access token, calls the LinkedIn API,
   and returns structured data ready to save to the user profile.

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

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/linkedin", tags=["linkedin"])

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

        # ── Attempt 2: OpenID userInfo headline fallback ────────────────────
        # Try /v2/userInfo (openid + profile scopes) for headline
        userinfo_res = await client.get(
            "https://api.linkedin.com/v2/userInfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        headline = ""
        if userinfo_res.status_code == 200:
            ui = userinfo_res.json()
            headline = ui.get("headline", "") or ""

        # ── Attempt 3: /v2/me with projection (r_liteprofile) ──────────────
        if not headline:
            me_res = await client.get(
                "https://api.linkedin.com/v2/me"
                "?projection=(id,localizedFirstName,localizedLastName,headline)",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_res.status_code == 200:
                headline = me_res.json().get("headline", "") or ""

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

    # Nothing was found
    return []


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
            # r_fullprofile not available — return empty list so frontend shows
            # a "no data found" message instead of an error crash.
            return []

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
