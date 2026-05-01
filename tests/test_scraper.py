"""Tests for ycai.scraper. Network-free — uses an httpx MockTransport."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from ycai.scraper import (
    UpstreamError,
    detect_latest_batch,
    fetch_batch,
    upstream_age_hours,
)


def _meta_payload() -> dict:
    return {
        "last_updated": "2026-02-08T01:49:11.368Z",
        "batches": {
            "winter-2025": {
                "count": 167,
                "api": "https://yc-oss.github.io/api/batches/winter-2025.json",
            },
            "winter-2026": {
                "count": 132,
                "api": "https://yc-oss.github.io/api/batches/winter-2026.json",
            },
            "fall-2025": {
                "count": 100,
                "api": "https://yc-oss.github.io/api/batches/fall-2025.json",
            },
        },
    }


def _company_payload() -> list[dict]:
    return [
        {
            "slug": "acme",
            "name": "Acme",
            "batch": "Winter 2026",
            "website": "https://acme.io",
            "one_liner": "AI for clouds",
            "long_description": "Acme builds AI-native ops tools.",
            "industry": "B2B",
            "industries": ["B2B"],
        },
        {
            "slug": "founder-data-leak",
            "name": "Leak Inc",
            "batch": "Winter 2026",
            "website": "https://leak.example",
            "one_liner": "PII test fixture",
            "long_description": "Reach the founder at founder@leak.example or call 555-867-5309.",
            "industry": "B2B",
            "industries": ["B2B"],
        },
    ]


def _mock_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/meta.json"):
        return httpx.Response(200, json=_meta_payload())
    if request.url.path.endswith("/winter-2026.json"):
        return httpx.Response(200, json=_company_payload())
    if request.url.path.endswith("/missing.json"):
        return httpx.Response(404, text="not found")
    if request.url.path.endswith("/malformed.json"):
        return httpx.Response(200, text="<not json>")
    return httpx.Response(404)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_mock_handler))


def test_detect_latest_batch_picks_winter_2026() -> None:
    with _client() as client:
        from ycai.scraper import fetch_meta

        meta = fetch_meta(client=client)
    batch = detect_latest_batch(meta=meta)
    assert batch.slug == "winter-2026"
    assert batch.label == "Winter 2026"
    assert batch.upstream_count == 132


def test_fetch_batch_returns_validated_companies(tmp_path) -> None:
    with _client() as client:
        meta, companies = fetch_batch(slug="winter-2026", client=client, cache_dir=tmp_path)
    assert meta.slug == "winter-2026"
    assert len(companies) == 2
    assert companies[0].slug == "acme"


def test_fetch_batch_sanitizes_pii_before_caching(tmp_path) -> None:
    with _client() as client:
        _meta, companies = fetch_batch(slug="winter-2026", client=client, cache_dir=tmp_path)
    leak = next(c for c in companies if c.slug == "founder-data-leak")
    assert "[REDACTED_EMAIL]" in leak.long_description
    assert "[REDACTED_PHONE]" in leak.long_description
    raw = json.loads((tmp_path / "yc_companies.json").read_text())
    leak_raw = next(c for c in raw if c["slug"] == "founder-data-leak")
    assert "founder@leak.example" not in leak_raw["long_description"]
    assert "555-867-5309" not in leak_raw["long_description"]


def test_fetch_batch_unknown_slug_raises_upstream_error() -> None:
    with _client() as client, pytest.raises(UpstreamError):
        fetch_batch(slug="winter-2099", client=client)


def test_fetch_batch_404_raises_upstream_error() -> None:
    # Manually construct a meta that points at a 404 URL.
    meta = {
        "last_updated": "2026-01-01T00:00:00Z",
        "batches": {"missing": {"count": 0, "api": "https://yc-oss.github.io/api/batches/missing.json"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/meta.json"):
            return httpx.Response(200, json=meta)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(UpstreamError):
        fetch_batch(slug="missing", client=client)


def test_upstream_age_hours_rounds_to_one_decimal() -> None:
    last_update = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    now = datetime(2026, 5, 2, 12, 30, tzinfo=UTC)
    assert upstream_age_hours(last_update, now=now) == 36.5


def test_upstream_age_hours_returns_none_for_unknown() -> None:
    assert upstream_age_hours(None) is None
