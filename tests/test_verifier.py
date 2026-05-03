"""Tests for ycai.verifier — link-checker contract.

Most of the verifier is integration territory (needs a real HTTP server),
so these tests focus on the publish-gate-relevant invariants:
the SSRF guard fires before any network call, and the ``blocked``
status is reported distinctly from ``dead``.
"""

from __future__ import annotations

import asyncio

from ycai.verifier import check_urls_async


def test_verifier_blocks_loopback_url() -> None:
    """A cited URL pointing at loopback must be flagged ``blocked`` —
    distinct from ``dead`` so the publish-gate failure log distinguishes
    'we refused' from 'the server said no'.
    """
    results = asyncio.run(check_urls_async(["http://127.0.0.1:1/"]))
    status, reason = results["http://127.0.0.1:1/"]
    assert status == "blocked"
    assert "internal" in reason


def test_verifier_blocks_rfc1918() -> None:
    results = asyncio.run(check_urls_async(["http://10.0.0.1/foo"]))
    assert results["http://10.0.0.1/foo"][0] == "blocked"


def test_verifier_blocks_metadata_endpoint() -> None:
    """169.254.169.254 is the AWS/GCP/Azure instance-metadata endpoint."""
    results = asyncio.run(check_urls_async(["http://169.254.169.254/latest/meta-data/"]))
    assert results["http://169.254.169.254/latest/meta-data/"][0] == "blocked"


def test_verifier_handles_empty_input() -> None:
    assert asyncio.run(check_urls_async([])) == {}
