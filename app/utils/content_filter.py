"""
Content restriction filter for chat messages.
Blocks/censors phone numbers, email addresses, URLs, social handles,
explicit contact-sharing phrases, profanity, and 18+ adult content so 
users cannot exchange personal contact details or share explicit material 
inside the platform chat.

Violations are replaced with *** by sanitize_content() rather than
being rejected outright — both layers (frontend + backend) apply this.
"""
import re
from typing import Optional

from app.utils.profanity_filter import contains_profanity, BAD_WORDS

# ── Contact-info patterns ──────────────────────────────────────────────────────

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

# ── 18+ / Adult content patterns ──────────────────────────────────────────────

_ADULT = re.compile(
    r"\b("
    # Explicit sexual acts / anatomy
    r"sex|sexy|sexual|sexually|intercourse|orgasm|climax|erotic|erotica"
    r"|masturbat\w*|fingering|blowjob|handjob|cumshot|cum\b|jizz"
    r"|penis|vagina|dick\b|cock\b|pussy\b|boobs?\b|tits?\b|ass\b|arse\b"
    r"|nipple\w*|boner|hardon|hard.?on|bdsm|fetish|kinky|kink\b"
    r"|naked|nude|nudity|nudes\b|nsfw"
    r"|horny|aroused|wet\b|turned.?on|sleep\s+with\s+me|fuck\w*|fck\b|fcuk\b"
    r"|shit\b|shitting|bullshit|wtf|stfu|slut\b|whore\b|bitch\b|bastard\b"
    r"|rape\w*|molest\w*|porn\w*|xxx\b|onlyfans|strip\s*club|prostitut\w*"
    r"|escort\b|hookup|hook.?up|one.?night.?stand|friends?\s+with\s+benefits|fwb\b"
    r"|sexting|sext\b"
    # Drug references
    r"|cocaine|heroin|meth\b|methamphetamine|mdma\b|ecstasy|lsd\b|molly\b|weed\b|marijuana"
    r"|cannabis\b|stoned\b|getting\s+high|roll\s+a\s+joint|smoke\s+weed"
    r")\b",
    re.IGNORECASE,
)

# ── Ordered list for blocking check (check_content) ───────────────────────────

_BLOCK_CHECKS: list[tuple[re.Pattern, str]] = [
    (_PHONE,         "Phone numbers are not allowed in chat."),
    (_EMAIL,         "Email addresses are not allowed in chat."),
    (_URL,           "Links are not allowed in chat."),
    (_AT_HANDLE,     "Social media handles are not allowed in chat."),
    (_SHARE_PHRASES, "Sharing personal contact details is not allowed in chat."),
    (_ADULT,         "Explicit or adult content is not allowed in chat."),
]

# Patterns that get redacted to *** rather than causing a hard block
_REDACT_PATTERNS: list[re.Pattern] = [
    _PHONE,
    _EMAIL,
    _URL,
    _AT_HANDLE,
    _SHARE_PHRASES,
    _ADULT,
]


def check_content(text: str) -> Optional[str]:
    """
    Returns a human-readable violation message if the text contains restricted
    content, or None if it is clean.  Used on WS messages and REST edits as a
    fast pre-check; call sanitize_content() to auto-censor before storing.
    """
    for pattern, message in _BLOCK_CHECKS:
        if pattern.search(text):
            return message
    
    # Check for profanity
    if contains_profanity(text):
        return "Inappropriate language is not allowed in chat."
    
    return None


def sanitize_content(text: str) -> str:
    """
    Returns the text with all restricted content replaced by ***.
    Applies every pattern in sequence so overlapping matches are all censored.
    Also filters profanity/bad words.
    """
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("***", text)
    
    # Filter profanity
    text_lower = text.lower()
    for word in BAD_WORDS:
        pattern = r'\b' + re.escape(word) + r'\b'
        matches = list(re.finditer(pattern, text_lower, re.IGNORECASE))
        for match in reversed(matches):  # Reverse to maintain indices
            start, end = match.span()
            text = text[:start] + "***" + text[end:]
            text_lower = text_lower[:start] + "***" + text_lower[end:]
    
    return text


def has_violation(text: str) -> bool:
    """Quick boolean check — True if any pattern matches or contains profanity."""
    return any(p.search(text) for p in _REDACT_PATTERNS) or contains_profanity(text)
