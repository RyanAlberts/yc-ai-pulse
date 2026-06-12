"""Tests for ycai.safe_http — the SSRF guard.

The whole point of this module is to refuse outbound fetches to internal
targets. These tests pin the contract so a future relaxation of the
allow/block logic is caught immediately.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ycai.safe_http import _ip_is_public, is_safe_external_url

# ----- block-list ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.1.2.3:8080/path",
        "http://localhost/",
        "http://[::1]/",
        # RFC1918
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        # link-local (includes EC2/GCP metadata 169.254.169.254)
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.0.1/",
        # IPv6 link-local
        "http://[fe80::1]/",
        # multicast
        "http://224.0.0.1/",
        # cloud metadata hostnames
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata.goog/foo",
        # unspecified
        "http://0.0.0.0/",
    ],
)
def test_blocks_internal_targets(url: str) -> None:
    assert is_safe_external_url(url) is False, f"should have blocked: {url}"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://internal.example/",
        "gopher://attacker/",
        "javascript:alert(1)",
        "",
        "not a url",
    ],
)
def test_blocks_unsupported_schemes(url: str) -> None:
    assert is_safe_external_url(url) is False, f"should have blocked: {url}"


# ----- allow-list ---------------------------------------------------------------


def test_allows_reserved_test_tlds_without_resolution() -> None:
    """RFC 2606 reserved TLDs cannot resolve to anything real, so they're
    safe by definition. This makes test fixtures using ``.example``
    reachable without monkey-patching every test.
    """
    assert is_safe_external_url("https://acme.example/foo") is True
    assert is_safe_external_url("https://acme.test/") is True
    assert is_safe_external_url("https://acme.invalid/") is True


def test_blocks_localhost_tld_despite_being_reserved() -> None:
    """``.localhost`` resolves to 127.0.0.1 — must NOT be on the allow-list."""
    assert is_safe_external_url("http://service.localhost/") is False


def test_allows_public_ip() -> None:
    """A literal public IP should pass without DNS."""
    # 8.8.8.8 (Google DNS) is the canonical public IP.
    assert is_safe_external_url("http://8.8.8.8/") is True


def test_allows_public_hostname_via_dns() -> None:
    """Real public hostname resolves to public IPs."""
    with patch("ycai.safe_http._resolve_all", return_value=["93.184.216.34"]):
        assert is_safe_external_url("https://example.com/") is True


def test_blocks_dns_failure() -> None:
    """If DNS fails (host doesn't exist), refuse the fetch.

    Reserved TLDs are exempt (handled elsewhere); this is for a host like
    ``totally-bogus-host-12345.com`` that just doesn't exist.
    """
    with patch("ycai.safe_http._resolve_all", return_value=[]):
        assert is_safe_external_url("https://totally-bogus.com/") is False


def test_blocks_dns_rebinding_to_private_ip() -> None:
    """A 'public-looking' host that resolves to a private IP must be blocked."""
    with patch("ycai.safe_http._resolve_all", return_value=["10.0.0.42"]):
        assert is_safe_external_url("https://attacker-dns-rebind.com/") is False


def test_blocks_when_any_resolution_is_private() -> None:
    """A multi-record hostname where ONE record is private — block it.

    This defends against round-robin DNS that mixes public and private
    addresses to confuse a permissive filter.
    """
    with patch("ycai.safe_http._resolve_all", return_value=["8.8.8.8", "10.0.0.1"]):
        assert is_safe_external_url("https://mixed-records.com/") is False


# ----- _ip_is_public -----------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),  # noqa: S104  - the unspecified address is a safety target, not a bind point
        ("::1", False),
        ("fe80::1", False),
        ("not-an-ip", False),
    ],
)
def test_ip_is_public(ip: str, expected: bool) -> None:
    assert _ip_is_public(ip) is expected
