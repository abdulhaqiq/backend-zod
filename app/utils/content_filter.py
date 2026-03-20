"""
Content restriction filter for chat messages.
Blocks phone numbers, email addresses, URLs, social handles, and explicit
contact-sharing phrases so users cannot exchange personal contact details
inside the platform chat.
"""
import re
from typing import Optional

# Phone numbers: +1 (555) 123-4567 · 555.123.4567 · 07911 123456 · 10-11 digit runs
_PHONE = re.compile(
    r"(\+?\d{1,3}[\s\-]?)?(\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}|\b\d{10,11}\b)",
)

# Email addresses
_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# HTTP / www links
_URL = re.compile(
    r"(https?://|www\.)\S+",
    re.IGNORECASE,
)

# @handle (social media username)
_AT_HANDLE = re.compile(
    r"(?<![a-zA-Z0-9])@[a-zA-Z0-9_.]{2,}",
)

# Explicit contact-sharing phrases
_SHARE_PHRASES = re.compile(
    r"\b(my\s+(number|phone|cell|mobile|ig|insta|snap|whatsapp|telegram|handle|username)"
    r"|(text|call|reach|dm|message|add)\s+me(\s+(on|at|via))?"
    r"|hit\s+me\s+up|hmu)\b",
    re.IGNORECASE,
)

# Map pattern → user-facing message
_CHECKS: list[tuple[re.Pattern, str]] = [
    (_PHONE,         "Phone numbers are not allowed in chat."),
    (_EMAIL,         "Email addresses are not allowed in chat."),
    (_URL,           "Links are not allowed in chat."),
    (_AT_HANDLE,     "Social media handles are not allowed in chat."),
    (_SHARE_PHRASES, "Sharing personal contact details is not allowed in chat."),
]


def check_content(text: str) -> Optional[str]:
    """
    Returns a human-readable violation message if the text contains restricted
    content, or None if it is clean.
    """
    for pattern, message in _CHECKS:
        if pattern.search(text):
            return message
    return None
