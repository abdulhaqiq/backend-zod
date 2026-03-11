import asyncio
import logging
from functools import partial

from app.core.config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _is_twilio_configured() -> bool:
    return (
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_ACCOUNT_SID != _PLACEHOLDER_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_AUTH_TOKEN != "your_twilio_auth_token"
        and settings.TWILIO_FROM_SMS
        and settings.TWILIO_FROM_SMS != "+1xxxxxxxxxx"
    )


def _send_sync(to: str, body: str, from_: str) -> str:
    from twilio.rest import Client
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    message = client.messages.create(to=to, from_=from_, body=body)
    return message.sid


async def send_otp(phone: str, code: str, channel: str) -> str:
    """
    Send a 5-digit OTP via Twilio SMS or WhatsApp.
    When Twilio credentials are not yet configured, logs the OTP to the
    console instead so developers can test the flow without a Twilio account.
    Returns the Twilio message SID (or a mock SID in dev mode).
    """
    body = (
        f"Your Mil verification code is: {code}. "
        f"It expires in {settings.OTP_EXPIRE_MINUTES} minutes. "
        f"Do not share it with anyone."
    )

    if not _is_twilio_configured():
        # ── DEV MODE ─────────────────────────────────────────────────────────
        logger.warning("=" * 60)
        logger.warning("  TWILIO NOT CONFIGURED — DEV MODE OTP LOG")
        logger.warning(f"  Phone   : {phone}")
        logger.warning(f"  Channel : {channel}")
        logger.warning(f"  OTP CODE: {code}")
        logger.warning("=" * 60)
        print(f"\n{'='*60}")
        print(f"  [DEV] OTP for {phone} via {channel}: {code}")
        print(f"{'='*60}\n")
        return "DEV-MODE-SID"

    # ── PRODUCTION ────────────────────────────────────────────────────────────
    if channel == "whatsapp":
        to = f"whatsapp:{phone}"
        from_ = settings.TWILIO_FROM_WHATSAPP
    else:
        to = phone
        from_ = settings.TWILIO_FROM_SMS

    loop = asyncio.get_event_loop()
    sid = await loop.run_in_executor(None, partial(_send_sync, to, body, from_))
    return sid
