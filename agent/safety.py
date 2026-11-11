"""Guardrails shared by every node: untrusted-data delimiting, PII masking, injection heuristics.

Job posts and client messages are DATA. They are wrapped in <untrusted_*> tags with any look-alike tags
neutralised, and every prompt says instructions inside those tags are never followed (agent/prompts/*).
"""

from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
SECRET = re.compile(r"\b(sk-[\w-]{10,}|gsk_[\w]{10,}|hf_[\w]{10,}|gh[pousr]_[\w]{10,}|Bearer\s+[\w.-]{10,})")

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
    r"\b(system|admin) (message|prompt|instruction)",
    r"if you are an? (ai|language model|llm|assistant|bot)",
    r"note to (any )?ai",
    r"(reveal|copy|print|show).{0,30}(system prompt|hidden instructions)",
    r"</?\s*(job_post|untrusted|system|instructions?)\s*>",
    r"new instruction",
    r"\bassistant\s*:",
    r"begin your (proposal|reply|response) with",
    r"(set|change) the (bid )?price to",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.I)


def mask_pii(text: str | None) -> str:
    """Mask emails, phone numbers, card/IBAN-like numbers and secrets. Used on every log line and trace."""
    if not text:
        return text or ""
    t = SECRET.sub("[secret]", str(text))
    t = EMAIL.sub("[email]", t)
    t = CARD.sub("[card]", t)
    t = IBAN.sub("[iban]", t)
    t = PHONE.sub("[phone]", t)
    return t


def injection_signals(text: str) -> list[str]:
    return sorted({m.group(0).lower()[:60] for m in _INJ.finditer(text or "")})


def wrap_untrusted(kind: str, text: str, limit: int = 4000) -> str:
    """Delimit untrusted content. Look-alike closing tags inside the data are defanged first."""
    t = (text or "")[:limit]
    t = re.sub(r"<\s*/?\s*(untrusted[\w_]*|job_post|system|instructions?)\s*>", "[tag removed]", t, flags=re.I)
    return f"<untrusted_{kind}>\n{t}\n</untrusted_{kind}>"
