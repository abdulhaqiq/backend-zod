"""
Marketing notification admin endpoints.

All routes sit under /admin/marketing and require is_admin=True.

  GET  /admin/marketing/countries            — list countries (filterable)
  POST /admin/marketing/countries            — create country entry
  PATCH /admin/marketing/countries/{id}      — update peak_hours / is_active / language
  GET  /admin/marketing/templates            — list templates
  POST /admin/marketing/templates            — create template
  PATCH /admin/marketing/templates/{id}      — update template
  DELETE /admin/marketing/templates/{id}     — soft-delete (is_active=False)
  GET  /admin/marketing/campaigns            — send history (paginated)
  POST /admin/marketing/campaigns/send       — execute a send NOW
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.push import notify_user, send_push_notification
from app.db.session import get_db
from app.models.marketing import MarketingCampaign, MarketingCountry, MarketingTemplate
from app.models.user import User

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/marketing", tags=["admin-marketing"])


async def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user


# ── Language detection helpers ─────────────────────────────────────────────────

# Maps lookup_option label substrings → ISO 639-1 language codes.
# Checked case-insensitively against the label of each language lookup.
_LABEL_TO_LANG: dict[str, str] = {
    "arabic":      "ar",
    "french":      "fr",
    "spanish":     "es",
    "portuguese":  "pt",
    "hindi":       "hi",
    "german":      "de",
    "italian":     "it",
    "dutch":       "nl",
    "turkish":     "tr",
    "urdu":        "ur",
    "bengali":     "bn",
    "persian":     "fa",
    "russian":     "ru",
    "chinese":     "zh",
    "japanese":    "ja",
    "korean":      "ko",
    "malay":       "ms",
    "indonesian":  "id",
    "swahili":     "sw",
}

_lang_lookup_cache: dict[int, str] | None = None  # lookup_option_id → language_code


async def _load_lang_lookup(db: AsyncSession) -> dict[int, str]:
    global _lang_lookup_cache
    if _lang_lookup_cache is not None:
        return _lang_lookup_cache
    rows = await db.execute(
        text("SELECT id, label FROM lookup_options WHERE category = 'language' AND is_active = true")
    )
    mapping: dict[int, str] = {}
    for row in rows.fetchall():
        label_lower = (row[1] or "").lower()
        for keyword, code in _LABEL_TO_LANG.items():
            if keyword in label_lower:
                mapping[int(row[0])] = code
                break
    _lang_lookup_cache = mapping
    return mapping


def _detect_language(user_languages: list | None, lang_map: dict[int, str], fallback: str) -> str:
    """Return the best ISO 639-1 code for a user given their languages JSONB list."""
    if user_languages:
        for entry in user_languages:
            lid = int(entry["id"]) if isinstance(entry, dict) else int(entry)
            if lid in lang_map:
                return lang_map[lid]
    return fallback


async def _pick_template(
    db: AsyncSession,
    language_code: str,
    notif_type: str = "promotions",
) -> MarketingTemplate | None:
    """Return an active template for the given language, falling back to 'en'."""
    result = await db.execute(
        select(MarketingTemplate)
        .where(
            MarketingTemplate.language_code == language_code,
            MarketingTemplate.notif_type == notif_type,
            MarketingTemplate.is_active.is_(True),
        )
        .limit(1)
    )
    tmpl = result.scalar_one_or_none()
    if tmpl is None and language_code != "en":
        result = await db.execute(
            select(MarketingTemplate)
            .where(
                MarketingTemplate.language_code == "en",
                MarketingTemplate.notif_type == notif_type,
                MarketingTemplate.is_active.is_(True),
            )
            .limit(1)
        )
        tmpl = result.scalar_one_or_none()
    return tmpl


# ── Send helper ────────────────────────────────────────────────────────────────

async def _execute_send(
    db: AsyncSession,
    *,
    target: str,
    target_value: str | None,
    template_id: int | None,
    custom_title: str | None,
    custom_body: str | None,
    language_override: str | None,
    triggered_by: str,
    campaign_name: str | None,
    scheduler_tz: str | None = None,
    scheduler_hour: int | None = None,
) -> dict[str, Any]:
    """
    Core send logic used by both the manual endpoint and the scheduler.
    Returns {"sent": N, "failed": M, "campaign_id": id}.
    """
    now_utc = datetime.now(timezone.utc)

    # ── Resolve template ──────────────────────────────────────────────────────
    # auto_template=True means each user gets a template matched to their language
    auto_template = (template_id is None and not (custom_title and custom_body))
    tmpl: MarketingTemplate | None = None
    if template_id:
        result = await db.execute(
            select(MarketingTemplate).where(MarketingTemplate.id == template_id)
        )
        tmpl = result.scalar_one_or_none()
        if not tmpl:
            raise HTTPException(status_code=404, detail="Template not found.")

    if not auto_template and not tmpl and not (custom_title and custom_body):
        raise HTTPException(
            status_code=422,
            detail="Provide either template_id or both custom_title and custom_body.",
        )

    # ── Resolve recipients ────────────────────────────────────────────────────
    base_q = select(User).where(
        User.push_token.isnot(None),
        User.push_token != "",
        User.notif_promotions.is_(True),
        User.is_active.is_(True),
    )

    if target == "all":
        result = await db.execute(base_q)
        recipients: list[User] = list(result.scalars().all())

    elif target == "country":
        if not target_value:
            raise HTTPException(status_code=422, detail="target_value (country code) required.")
        result = await db.execute(
            base_q.where(User.country.ilike(f"%{target_value}%"))
        )
        recipients = list(result.scalars().all())

    elif target == "region":
        if not target_value:
            raise HTTPException(status_code=422, detail="target_value (region name) required.")
        country_result = await db.execute(
            select(MarketingCountry.code).where(
                MarketingCountry.region.ilike(f"%{target_value}%"),
                MarketingCountry.is_active.is_(True),
            )
        )
        country_codes = [r[0] for r in country_result.fetchall()]
        if not country_codes:
            raise HTTPException(status_code=404, detail=f"No active countries found for region '{target_value}'.")
        # Build OR filter for all country codes
        from sqlalchemy import or_
        result = await db.execute(
            base_q.where(
                or_(*[User.country.ilike(f"%{code}%") for code in country_codes])
            )
        )
        recipients = list(result.scalars().all())

    elif target == "email":
        if not target_value:
            raise HTTPException(status_code=422, detail="target_value (email) required.")
        result = await db.execute(
            select(User).where(User.email == target_value.strip())
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail=f"No user found with email {target_value}")
        recipients = [user] if user.push_token else []

    elif target == "phone":
        if not target_value:
            raise HTTPException(status_code=422, detail="target_value (phone) required.")
        result = await db.execute(
            select(User).where(User.phone == target_value.strip())
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail=f"No user found with phone {target_value}")
        recipients = [user] if user.push_token else []

    else:
        raise HTTPException(status_code=422, detail=f"Unknown target '{target}'.")

    # ── Load language lookup (for auto-detect) ────────────────────────────────
    lang_map = await _load_lang_lookup(db)

    # ── Send ──────────────────────────────────────────────────────────────────
    sent = 0
    failed = 0
    
    # Track which users AND push tokens we've sent to in last 12 hours (prevent duplicates)
    # This prevents race conditions when multiple countries trigger at the same time
    # Extended to 12 hours to prevent notification fatigue
    cutoff = now_utc - timedelta(hours=12)
    recent_sends = await db.execute(
        text("SELECT DISTINCT user_id FROM user_marketing_sends WHERE sent_at >= :cutoff"),
        {"cutoff": cutoff}
    )
    recently_sent_users = {row[0] for row in recent_sends.fetchall()}
    
    # Also check recently sent push tokens (global deduplication across all campaigns)
    recent_tokens = await db.execute(
        text("""
            SELECT DISTINCT u.push_token 
            FROM user_marketing_sends ums
            JOIN users u ON u.id = ums.user_id
            WHERE ums.sent_at >= :cutoff AND u.push_token IS NOT NULL
        """),
        {"cutoff": cutoff}
    )
    recently_sent_tokens = {row[0] for row in recent_tokens.fetchall()}

    # Deduplicate recipients by push_token (same device = one notification)
    # Group users by push_token to send only once per device
    token_to_users: dict[str, list[User]] = {}
    for user in recipients:
        if not user.push_token or user.id in recently_sent_users:
            continue
        # CRITICAL: Skip if this push token received ANY notification in last 3 hours
        if user.push_token in recently_sent_tokens:
            continue
        if user.push_token not in token_to_users:
            token_to_users[user.push_token] = []
        token_to_users[user.push_token].append(user)

    for push_token, users_on_device in token_to_users.items():
        # Pick the first user as representative (for language detection)
        user = users_on_device[0]

        # Determine language for this user
        # Use country's primary_language as fallback (country field may be full name like "India")
        lang = language_override or _detect_language(user.languages, lang_map, "en")

        # Resolve title/body
        if auto_template:
            # Auto-pick best template for this user's language
            use_tmpl = await _pick_template(db, lang)
            if not use_tmpl:
                failed += 1
                continue
            use_title = use_tmpl.title
            use_body = use_tmpl.body
            use_data = use_tmpl.data
            use_notif_type = use_tmpl.notif_type
        elif tmpl:
            # If override language differs from template language, try matching template
            if tmpl.language_code != lang:
                alt = await _pick_template(db, lang, notif_type=tmpl.notif_type)
                use_title = (alt or tmpl).title
                use_body = (alt or tmpl).body
                use_data = (alt or tmpl).data
                use_notif_type = (alt or tmpl).notif_type
            else:
                use_title = tmpl.title
                use_body = tmpl.body
                use_data = tmpl.data
                use_notif_type = tmpl.notif_type
        else:
            use_title = custom_title  # type: ignore[assignment]
            use_body = custom_body    # type: ignore[assignment]
            use_data = None
            use_notif_type = "promotions"

        try:
            await send_push_notification(
                push_token,
                title=use_title,
                body=use_body,
                data={**(use_data or {}), "type": use_notif_type},
                channel_id="marketing",
                priority="normal",
                notif_type=use_notif_type,
            )
            sent += 1
            # Mark ALL users on this device as having received the notification
            for u in users_on_device:
                await db.execute(
                    text("INSERT INTO user_marketing_sends (user_id, sent_at) VALUES (:uid, :now) ON CONFLICT DO NOTHING"),
                    {"uid": str(u.id), "now": now_utc}
                )
        except Exception as exc:
            _log.warning("marketing | send failed token=%s users=%s: %s", push_token[:16], [str(u.id)[:8] for u in users_on_device], exc)
            failed += 1

    # ── Persist campaign log ──────────────────────────────────────────────────
    campaign = MarketingCampaign(
        name=campaign_name,
        template_id=template_id,
        custom_title=custom_title if not tmpl else None,
        custom_body=custom_body if not tmpl else None,
        target=target,
        target_value=target_value,
        language_code=language_override or (tmpl.language_code if tmpl else "en"),
        scheduler_tz=scheduler_tz,
        scheduler_hour=scheduler_hour,
        status="sent" if failed == 0 else ("failed" if sent == 0 else "partial"),
        triggered_by=triggered_by,
        sent_count=sent,
        failed_count=failed,
        sent_at=now_utc,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    _log.info(
        "marketing | campaign=%d triggered_by=%s target=%s/%s sent=%d failed=%d",
        campaign.id, triggered_by, target, target_value, sent, failed,
    )
    return {"sent": sent, "failed": failed, "campaign_id": campaign.id}


# ── Schemas ────────────────────────────────────────────────────────────────────

class CountryCreate(BaseModel):
    name: str
    code: str
    region: str
    tz_name: str
    peak_hours: list[int]
    primary_language: str = "en"
    is_active: bool = True


class CountryUpdate(BaseModel):
    name: str | None = None
    peak_hours: list[int] | None = None
    primary_language: str | None = None
    is_active: bool | None = None


class TemplateCreate(BaseModel):
    name: str
    language_code: str = "en"
    title: str
    body: str
    notif_type: Literal["promotions", "dating_tips"] = "promotions"
    data: dict | None = None
    is_active: bool = True


class TemplateUpdate(BaseModel):
    name: str | None = None
    language_code: str | None = None
    title: str | None = None
    body: str | None = None
    notif_type: str | None = None
    data: dict | None = None
    is_active: bool | None = None


class SendRequest(BaseModel):
    target: Literal["all", "country", "region", "email", "phone"] = "all"
    target_value: str | None = None
    template_id: int | None = None
    custom_title: str | None = None
    custom_body: str | None = None
    language_override: str | None = None
    campaign_name: str | None = None


# ── Country endpoints ──────────────────────────────────────────────────────────

@router.get("/countries", summary="List marketing countries")
async def list_countries(
    region: str | None = Query(None),
    active_only: bool = Query(False),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(MarketingCountry).order_by(MarketingCountry.region, MarketingCountry.name)
    if region:
        q = q.where(MarketingCountry.region.ilike(f"%{region}%"))
    if active_only:
        q = q.where(MarketingCountry.is_active.is_(True))
    result = await db.execute(q)
    countries = result.scalars().all()
    return {
        "total": len(countries),
        "countries": [
            {
                "id": c.id, "name": c.name, "code": c.code, "region": c.region,
                "tz_name": c.tz_name, "peak_hours": c.peak_hours,
                "primary_language": c.primary_language, "is_active": c.is_active,
            }
            for c in countries
        ],
    }


@router.post("/countries", summary="Create a marketing country entry")
async def create_country(
    body: CountryCreate,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    country = MarketingCountry(**body.model_dump())
    db.add(country)
    await db.commit()
    await db.refresh(country)
    return {"id": country.id, "name": country.name, "code": country.code}


@router.patch("/countries/{country_id}", summary="Update a marketing country entry")
async def update_country(
    country_id: int,
    body: CountryUpdate,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    country = await db.get(MarketingCountry, country_id)
    if not country:
        raise HTTPException(status_code=404, detail="Country not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(country, field, value)
    await db.commit()
    await db.refresh(country)
    return {"id": country.id, "name": country.name, "is_active": country.is_active, "peak_hours": country.peak_hours}


# ── Template endpoints ─────────────────────────────────────────────────────────

@router.get("/templates", summary="List marketing templates")
async def list_templates(
    language: str | None = Query(None),
    active_only: bool = Query(False),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(MarketingTemplate).order_by(MarketingTemplate.language_code, MarketingTemplate.name)
    if language:
        q = q.where(MarketingTemplate.language_code == language)
    if active_only:
        q = q.where(MarketingTemplate.is_active.is_(True))
    result = await db.execute(q)
    templates = result.scalars().all()
    return {
        "total": len(templates),
        "templates": [
            {
                "id": t.id, "name": t.name, "language_code": t.language_code,
                "title": t.title, "body": t.body, "notif_type": t.notif_type,
                "data": t.data, "is_active": t.is_active,
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in templates
        ],
    }


@router.post("/templates", summary="Create a marketing template")
async def create_template(
    body: TemplateCreate,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    tmpl = MarketingTemplate(**body.model_dump())
    db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)
    return {"id": tmpl.id, "name": tmpl.name, "language_code": tmpl.language_code}


@router.patch("/templates/{template_id}", summary="Update a marketing template")
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    tmpl = await db.get(MarketingTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(tmpl, field, value)
    tmpl.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(tmpl)
    return {"id": tmpl.id, "name": tmpl.name, "language_code": tmpl.language_code, "is_active": tmpl.is_active}


@router.delete("/templates/{template_id}", summary="Soft-delete a marketing template")
async def delete_template(
    template_id: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    tmpl = await db.get(MarketingTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found.")
    tmpl.is_active = False
    tmpl.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": tmpl.id, "deleted": True}


# ── Campaign history ───────────────────────────────────────────────────────────

@router.get("/campaigns", summary="List marketing campaign send history")
async def list_campaigns(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MarketingCampaign)
        .order_by(MarketingCampaign.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    campaigns = result.scalars().all()
    return {
        "campaigns": [
            {
                "id": c.id, "name": c.name, "template_id": c.template_id,
                "target": c.target, "target_value": c.target_value,
                "language_code": c.language_code,
                "status": c.status, "triggered_by": c.triggered_by,
                "sent_count": c.sent_count, "failed_count": c.failed_count,
                "sent_at": c.sent_at.isoformat() if c.sent_at else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in campaigns
        ],
    }


# ── Send now ───────────────────────────────────────────────────────────────────

@router.post("/campaigns/send", summary="Execute a marketing send immediately")
async def send_campaign(
    body: SendRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await _execute_send(
        db,
        target=body.target,
        target_value=body.target_value,
        template_id=body.template_id,
        custom_title=body.custom_title,
        custom_body=body.custom_body,
        language_override=body.language_override,
        triggered_by="admin",
        campaign_name=body.campaign_name,
    )
    _log.info(
        "Admin %s triggered marketing send: target=%s/%s sent=%d failed=%d",
        current_user.id, body.target, body.target_value, result["sent"], result["failed"],
    )
    return result
