"""Strip PII before any data hits disk or the LLM.

yc-oss/api currently does not include emails / phones / addresses, but this
defensive layer guards against (a) future schema additions, (b) PII in the
long_description field that founders sometimes paste in, and (c) crawled
website content during enrichment.
"""

from __future__ import annotations

import re

# Order matters: more specific patterns first so we don't double-redact.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL), "[REDACTED_KEY]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_API_KEY]"),
    # Emails — generic
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # mailto: links
    (re.compile(r"mailto:[^\s\"'<>]+"), "mailto:[REDACTED_EMAIL]"),
    # US-style phone numbers (loose). Avoid matching version numbers like 1.2.3 by
    # requiring at least two groups of 3-4 digits separated by hyphens / dots / spaces.
    (re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"), "[REDACTED_PHONE]"),
    # Street addresses (very loose — only catches obvious patterns)
    (
        re.compile(
            r"\b\d+\s+[A-Za-z][A-Za-z\s]{2,30}\s+"
            r"(Street|St|Avenue|Ave|Blvd|Boulevard|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct)\b\.?",
            re.IGNORECASE,
        ),
        "[REDACTED_ADDRESS]",
    ),
    # Suite numbers
    (re.compile(r"\b(Suite|Ste|Apt|Apartment|Unit)\s+#?\d+[A-Za-z]?\b", re.IGNORECASE), "[REDACTED_ADDRESS]"),
)


def strip_pii(text: str) -> str:
    """Return ``text`` with PII patterns redacted.

    Idempotent — running this twice yields the same string.
    """
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def sanitize_dict(data: dict[str, object]) -> dict[str, object]:
    """Recursively strip PII from string values in a JSON-shaped dict."""
    return {k: _sanitize_value(v) for k, v in data.items()}


def _sanitize_value(value: object) -> object:
    if isinstance(value, str):
        return strip_pii(value)
    if isinstance(value, dict):
        return sanitize_dict(value)
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    return value


__all__ = ["sanitize_dict", "strip_pii"]
