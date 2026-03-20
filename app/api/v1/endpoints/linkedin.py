import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/linkedin", tags=["linkedin"])


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


@router.post("/import-work", response_model=list[WorkEntry])
async def import_linkedin_work(
    body: LinkedInCodeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Exchange an authorization code for an access token, then fetch
    the user's LinkedIn work experience and return it as a list of
    WorkEntry objects ready to save to the profile.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        # ── 1. Exchange code for access token ─────────────────────────
        token_res = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": body.redirect_uri,
                "client_id": settings.LINKEDIN_CLIENT_ID,
                "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_res.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"LinkedIn token exchange failed: {token_res.text}",
        )

    token_data = token_res.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token returned by LinkedIn")

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        # ── 2. Fetch positions via LinkedIn API v2 ─────────────────────
        positions_res = await client.get(
            "https://api.linkedin.com/v2/positions?q=members&count=10",
            headers=headers,
        )

    entries: list[WorkEntry] = []

    if positions_res.status_code == 200:
        data = positions_res.json()
        for pos in data.get("elements", []):
            title = pos.get("title", {}).get("localized", {})
            title_str = next(iter(title.values()), "") if title else ""

            company_name = pos.get("companyName", {}).get("localized", {})
            company_str = next(iter(company_name.values()), "") if company_name else ""

            start_date = pos.get("startMonthYear", {})
            start_year = str(start_date.get("year", "")) if start_date else ""

            end_date = pos.get("endMonthYear")
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
    else:
        # Fallback: try the newer OpenID-based profile endpoint
        async with httpx.AsyncClient(timeout=15) as client:
            profile_res = await client.get(
                "https://api.linkedin.com/v2/me?projection=(id,localizedFirstName,localizedLastName,positions)",
                headers=headers,
            )
        # If we at least get a profile, return empty list (no positions scope)
        if profile_res.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail="Could not fetch LinkedIn profile. Make sure the app has r_liteprofile and r_fullprofile permissions.",
            )

    return entries


@router.post("/import-education", response_model=list[EduEntry])
async def import_linkedin_education(
    body: LinkedInCodeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Exchange an authorization code for an access token, then fetch
    the user's LinkedIn education and return it as a list of EduEntry
    objects ready to save to the profile.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        token_res = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": body.redirect_uri,
                "client_id": settings.LINKEDIN_CLIENT_ID,
                "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_res.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"LinkedIn token exchange failed: {token_res.text}",
        )

    access_token = token_res.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token returned by LinkedIn")

    headers = {"Authorization": f"Bearer {access_token}"}

    entries: list[EduEntry] = []

    async with httpx.AsyncClient(timeout=15) as client:
        edu_res = await client.get(
            "https://api.linkedin.com/v2/educations?q=members&count=10",
            headers=headers,
        )

    if edu_res.status_code == 200:
        for edu in edu_res.json().get("elements", []):
            school = edu.get("schoolName", {}).get("localized", {})
            institution = next(iter(school.values()), "") if school else ""

            field = edu.get("fieldOfStudy", {}).get("localized", {})
            course = next(iter(field.values()), "") if field else ""

            deg = edu.get("degreeName", {}).get("localized", {})
            degree = next(iter(deg.values()), "") if deg else ""

            end_date = edu.get("endMonthYear", {})
            grad_year = str(end_date.get("year", "")) if end_date else ""

            if institution or course:
                entries.append(EduEntry(
                    institution=institution,
                    course=course,
                    degree=degree,
                    grad_year=grad_year,
                ))
    else:
        raise HTTPException(
            status_code=400,
            detail="Could not fetch LinkedIn education. Make sure the app has r_fullprofile permission.",
        )

    return entries
