"""Tests for ycai.external_signals — the noise filters are the whole point.

Substring matching produced catastrophic false positives on common-word
company names ('Crow' matched 'CrowdStrike', 'Fort' matched 'Fortnite').
These tests pin the URL-host-alignment + word-boundary contract so a
future relaxation of the filter is caught immediately.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ycai.external_signals import (
    CompanyExternalProfile,
    ExternalSignal,
    _host_of,
    _name_token_re,
    _url_aligns,
    best_signal,
    fetch_for_company,
    quoted_url,
    signal_count,
)

# ----- pure helpers ---------------------------------------------------------------


def test_host_of_strips_www() -> None:
    assert _host_of("https://www.example.com/foo") == "example.com"
    assert _host_of("https://example.com/foo") == "example.com"
    assert _host_of("not a url") == ""
    assert _host_of("") == ""


def test_name_token_re_drops_short_names() -> None:
    assert _name_token_re("Crow") is None  # 4 chars, below min
    assert _name_token_re("AI") is None
    assert _name_token_re("RunAnywhere") is not None


def test_name_token_re_word_boundary_drops_substring_collisions() -> None:
    """The whole reason this module needed rewriting."""
    pat = _name_token_re("RunAnywhere")
    assert pat is not None
    assert pat.search("Launch HN: RunAnywhere (YC W26)") is not None
    # CrowdStrike must not match a Crow-like pattern (this is why min length
    # is 5 — but verify the regex is word-bounded for longer names too).
    pat2 = _name_token_re("Pocket")
    assert pat2 is not None
    # 'pocketed' should NOT match because of \b
    assert pat2.search("she pocketed the change") is None
    assert pat2.search("Pocket TTS launched today") is not None


def test_url_aligns() -> None:
    assert _url_aligns("https://www.usechamber.io/foo", ("usechamber.io",)) is True
    assert _url_aligns("https://news.ycombinator.com/item?id=123", ("usechamber.io",)) is False
    assert _url_aligns("", ("usechamber.io",)) is False
    assert _url_aligns("https://x.com/foo", ()) is False


# ----- network mocking helpers --------------------------------------------------


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=None,
                response=None,  # type: ignore[arg-type]
            )


class _StubClient:
    """Mock httpx.AsyncClient.get that returns canned responses keyed by URL prefix."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(url)
        for prefix, payload in self._responses.items():
            if url.startswith(prefix):
                return _Response(payload)
        return _Response({}, status_code=404)


# ----- HN signal filtering ------------------------------------------------------


@pytest.mark.asyncio
async def test_hn_url_host_aligned_keeps_real_yc_launch_post() -> None:
    """The Launch HN post for RunAnywhere links to runanywhere.ai → keep."""
    responses = {
        "https://hn.algolia.com/api/v1/search": {
            "hits": [
                {
                    "title": "Launch HN: RunAnywhere (YC W26) -Faster AI Inference on Apple Silicon",
                    "url": "https://www.runanywhere.ai/",
                    "points": 240,
                    "num_comments": 88,
                    "objectID": "abc123",
                }
            ]
        }
    }
    client = _StubClient(responses)
    profile = await fetch_for_company(client, "runanywhere", "RunAnywhere", website_host="runanywhere.ai")  # type: ignore[arg-type]
    assert len(profile.hn) == 1
    assert profile.hn[0].score == 240


@pytest.mark.asyncio
async def test_hn_drops_substring_collision_with_short_name() -> None:
    """'Crow' should not match 'CrowdStrike': name length below min, dropped at source."""
    responses = {
        "https://hn.algolia.com/api/v1/search": {
            "hits": [
                {
                    "title": "CrowdStrike Update: Windows Bluescreen and Boot Loops",
                    "url": "https://www.crowdstrike.com/",
                    "points": 4489,
                    "num_comments": 2300,
                    "objectID": "xyz",
                }
            ]
        }
    }
    client = _StubClient(responses)
    profile = await fetch_for_company(client, "crow", "Crow", website_host="crow-yc.example")  # type: ignore[arg-type]
    assert profile.hn == []


@pytest.mark.asyncio
async def test_hn_drops_unrelated_post_when_url_host_does_not_align() -> None:
    """'Pocket' the company should NOT capture 'Mozilla shuts down Pocket' — those are different Pockets."""
    responses = {
        "https://hn.algolia.com/api/v1/search": {
            "hits": [
                {
                    "title": "Mozilla to shut down Pocket and Fakespot",
                    "url": "https://blog.mozilla.org/post-1",
                    "points": 1222,
                    "num_comments": 700,
                    "objectID": "moz1",
                }
            ]
        }
    }
    client = _StubClient(responses)
    profile = await fetch_for_company(client, "pocket", "Pocket", website_host="usepocket.ai")  # type: ignore[arg-type]
    assert profile.hn == []


@pytest.mark.asyncio
async def test_hn_keeps_launch_post_via_yc_pattern() -> None:
    """Even without URL-host alignment, '(YC W26)' pattern is distinctive enough."""
    responses = {
        "https://hn.algolia.com/api/v1/search": {
            "hits": [
                {
                    "title": "Launch HN: SomeCompany (YC W26) -does X",
                    "url": "https://news.ycombinator.com/item?id=999",
                    "points": 50,
                    "num_comments": 10,
                    "objectID": "yc1",
                }
            ]
        }
    }
    client = _StubClient(responses)
    profile = await fetch_for_company(
        client,
        "somecompany",
        "SomeCompany",
        website_host="somecompany.example",  # type: ignore[arg-type]
    )
    assert len(profile.hn) == 1


@pytest.mark.asyncio
async def test_hn_skips_when_no_website_host_provided() -> None:
    """Without a host anchor we can't disambiguate. Bail rather than ship false positives."""
    responses = {
        "https://hn.algolia.com/api/v1/search": {
            "hits": [
                {
                    "title": "Launch HN: SomeCompany (YC W26)",
                    "url": "https://example.com/",
                    "points": 50,
                    "num_comments": 10,
                    "objectID": "x",
                }
            ]
        }
    }
    client = _StubClient(responses)
    profile = await fetch_for_company(client, "somecompany", "SomeCompany", website_host="")  # type: ignore[arg-type]
    assert profile.hn == []


# ----- Reddit signal filtering --------------------------------------------------


@pytest.mark.asyncio
async def test_reddit_keeps_post_when_external_url_aligns() -> None:
    responses = {
        "https://www.reddit.com/search.json": {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Sentrial caught a regression for us",
                            "permalink": "/r/devops/comments/abc/title/",
                            "url_overridden_by_dest": "https://sentrial.com/post",
                            "ups": 25,
                            "subreddit": "devops",
                            "selftext": "",
                        }
                    }
                ]
            }
        }
    }
    client = _StubClient(responses)
    profile = await fetch_for_company(client, "sentrial", "Sentrial", website_host="sentrial.com")  # type: ignore[arg-type]
    assert len(profile.reddit) == 1
    assert profile.reddit[0].url.startswith("https://www.reddit.com/r/devops/")


@pytest.mark.asyncio
async def test_reddit_drops_unrelated_homonym() -> None:
    responses = {
        "https://www.reddit.com/search.json": {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "I bought a cardboard box from amazon and",
                            "permalink": "/r/random/abc/",
                            "url_overridden_by_dest": "https://amazon.com/cardboard",
                            "ups": 25000,
                            "subreddit": "random",
                            "selftext": "",
                        }
                    }
                ]
            }
        }
    }
    client = _StubClient(responses)
    # The company "Cardboard" website is usecardboard.com; this Amazon post is unrelated.
    profile = await fetch_for_company(client, "cardboard", "Cardboard", website_host="usecardboard.com")  # type: ignore[arg-type]
    assert profile.reddit == []


# ----- profile aggregation ------------------------------------------------------


def test_signal_count_and_best_signal() -> None:
    profile = CompanyExternalProfile(slug="x")
    assert signal_count(profile) == 0
    assert best_signal(profile) is None

    profile.hn.append(ExternalSignal(slug="x", source="hn", title="t", url="u", score=10))
    profile.reddit.append(ExternalSignal(slug="x", source="reddit", title="t", url="u", score=20))
    profile.github = ExternalSignal(slug="x", source="github", title="t", url="u", score=5)
    assert signal_count(profile) == 3
    best = best_signal(profile)
    assert best is not None
    assert best.source == "reddit"


def test_quoted_url_passes_safe_chars() -> None:
    out = quoted_url("https://example.com/a?b=c&d=e#frag")
    assert "example.com" in out
    assert "?" in out
    assert "&" in out
