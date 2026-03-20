"""
Email service — used for university email OTP verification.

Sends via SMTP when credentials are configured in the environment.
Falls back to console logging in dev mode (no credentials needed).
"""
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import partial

from app.core.config import settings

logger = logging.getLogger(__name__)


def _is_smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD)


def _send_sync(to: str, subject: str, html: str, plain: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    msg["To"]      = to

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.EMAIL_FROM, to, msg.as_string())


async def send_university_otp(to_email: str, code: str, university: str | None) -> None:
    """
    Send a 6-digit OTP to the university email address.
    Logs to console in dev mode when SMTP is not configured.
    """
    uni_label = university or "your university"
    subject   = f"Verify your {uni_label} email — Zod"

    plain = (
        f"Your Zod university verification code is: {code}\n\n"
        f"This code expires in {settings.OTP_EXPIRE_MINUTES} minutes.\n"
        f"Do not share it with anyone."
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:40px auto;padding:32px;
                border-radius:16px;background:#0a0a0a;color:#fff;">
      <h2 style="margin:0 0 8px;font-size:22px;">Verify your university email</h2>
      <p style="color:#aaa;margin:0 0 28px;font-size:14px;">
        Confirming your <strong>{uni_label}</strong> email lets you connect
        with fellow students on Zod.
      </p>
      <div style="background:#1a1a1a;border-radius:12px;padding:24px;text-align:center;
                  font-size:36px;font-weight:700;letter-spacing:12px;color:#fff;">
        {code}
      </div>
      <p style="color:#666;font-size:12px;margin-top:20px;text-align:center;">
        Expires in {settings.OTP_EXPIRE_MINUTES} minutes. Do not share this code.
      </p>
    </div>
    """

    if not _is_smtp_configured():
        logger.warning("=" * 60)
        logger.warning("  SMTP NOT CONFIGURED — DEV MODE EMAIL LOG")
        logger.warning(f"  To      : {to_email}")
        logger.warning(f"  Subject : {subject}")
        logger.warning(f"  OTP CODE: {code}")
        logger.warning("=" * 60)
        print(f"\n{'='*60}")
        print(f"  [DEV] University OTP for {to_email}: {code}")
        print(f"{'='*60}\n")
        return

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_send_sync, to_email, subject, html, plain))
    logger.info("University OTP email sent to %s", to_email)
