"""
OpenAI Moderation API service for chat abuse detection.

Sends a conversation (list of message strings) to the OpenAI Moderation
endpoint and returns True if the content is flagged for harassment,
hate speech, or spam.

Required env var: OPENAI_API_KEY
"""
import logging

from openai import AsyncOpenAI

from app.core.config import settings

_log = logging.getLogger(__name__)


async def check_chat_for_abuse(messages: list[str]) -> bool:
    """Returns True if OpenAI Moderation API flags the conversation."""
    if not messages:
        return False

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    combined = "\n".join(messages)

    try:
        resp = await client.moderations.create(input=combined)
        flagged = resp.results[0].flagged
        if flagged:
            _log.warning("OpenAI Moderation flagged conversation (categories=%s)", resp.results[0].categories)
        return flagged
    except Exception as exc:
        _log.error("OpenAI Moderation check failed: %s", exc)
        return False
