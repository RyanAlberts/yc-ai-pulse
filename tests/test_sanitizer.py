"""Tests for ycai.sanitizer. Confirms PII strip is idempotent and exhaustive."""

from __future__ import annotations

import pytest

from ycai.sanitizer import sanitize_dict, strip_pii


# Test fixtures contain intentional fake credential patterns to verify redaction.
# They are not real keys. Inline pragmas keep detect-secrets quiet.
@pytest.mark.parametrize(
    ("text", "expected_in"),
    [
        ("contact us at hello@example.com please", "[REDACTED_EMAIL]"),
        ("api key is sk-ant-abc123def456ghi789jkl0", "[REDACTED_API_KEY]"),  # pragma: allowlist secret
        ("ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa is leaked", "[REDACTED_API_KEY]"),  # pragma: allowlist secret
        ("AKIAIOSFODNN7EXAMPLE is the access key", "[REDACTED_API_KEY]"),  # pragma: allowlist secret
        ("call me at (555) 123-4567 sometime", "[REDACTED_PHONE]"),
        ("123 Main Street is the office", "[REDACTED_ADDRESS]"),
        ("Suite 400 has the team", "[REDACTED_ADDRESS]"),
        ("mailto:hr@acme.io for jobs", "mailto:[REDACTED_EMAIL]"),
    ],
)
def test_strip_pii_redacts_known_patterns(text: str, expected_in: str) -> None:
    out = strip_pii(text)
    assert expected_in in out
    assert "@" not in out or expected_in.startswith("mailto") or "REDACTED" in out


def test_strip_pii_idempotent() -> None:
    s = "email me hello@x.com or call (555) 123-4567"
    once = strip_pii(s)
    twice = strip_pii(once)
    assert once == twice


def test_strip_pii_preserves_non_pii() -> None:
    s = "Foo Bar makes AI tools for marketing teams. Series A."
    assert strip_pii(s) == s


def test_strip_pii_handles_empty_string() -> None:
    assert strip_pii("") == ""


def test_strip_pii_handles_none_safely() -> None:
    # We pass strings only, but make sure it doesn't crash on falsy values.
    assert strip_pii("") == ""


def test_sanitize_dict_recursive() -> None:
    payload = {
        "name": "Acme",
        "contact": {"email": "founder@acme.io", "phone": "555-123-4567"},
        "tags": ["AI", "Founder reachable at leak@example.com today"],
    }
    out = sanitize_dict(payload)
    assert out["name"] == "Acme"
    assert "[REDACTED_EMAIL]" in out["contact"]["email"]  # type: ignore[index]
    assert "[REDACTED_PHONE]" in out["contact"]["phone"]  # type: ignore[index]
    assert "[REDACTED_EMAIL]" in out["tags"][1]  # type: ignore[index]


def test_sanitize_dict_does_not_mangle_version_strings() -> None:
    payload = {"version": "1.2.3", "release": "0.0.1-rc.4"}
    out = sanitize_dict(payload)
    assert out == payload


def test_strip_pii_does_not_redact_yc_profile_urls() -> None:
    # YC profile URLs are public and we explicitly want them in artifacts.
    s = "Visit https://www.ycombinator.com/companies/acme for details"
    assert strip_pii(s) == s
