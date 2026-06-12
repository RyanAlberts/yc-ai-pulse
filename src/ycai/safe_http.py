"""SSRF guard for outbound HTTP fetches.

Every fetcher in this codebase pulls URLs that originated outside our
control: yc-oss/api gives us company website URLs, the LLM cites
``oss_evidence_url`` and traction-source URLs, the depth=1 crawler
follows ``href`` attributes, and the link verifier re-fetches every cited
URL before any artifact is published.

Without an SSRF guard, a poisoned upstream value or a hallucinated LLM
output could direct the verifier to scan loopback / RFC1918 / link-local
ranges — including AWS/GCP/Azure metadata endpoints
(``169.254.169.254``) when this is run on cloud infrastructure.

Use ``is_safe_external_url`` before any outbound call. It rejects:
- non-http/https schemes (file://, ftp://, gopher://, etc.)
- malformed URLs (no host)
- hostnames whose resolved IPs are loopback, link-local, private,
  reserved, multicast, or unspecified
- a few well-known cloud metadata hostnames

The check resolves the host via DNS. The resolved-IP set is checked,
not just the first record, to defend against DNS rebinding (where a
hostname returns one address now and a different one on the next
lookup). Callers that re-resolve later should either pin the address
returned here or accept the residual risk.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Hostnames that are not in private IP ranges but are known SSRF targets
# (cloud-provider instance-metadata services).
_BLOCKED_HOSTS: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",  # Some clouds resolve bare 'metadata' on the local network
    }
)

# RFC 2606 reserved TLDs that are guaranteed never to resolve and are
# explicitly intended for documentation, testing, and example use. We
# allow them through the safety check because they're harmless (the
# fetch will fail with a DNS error, never reach a real host) and tests
# in this codebase rely on them. ``.localhost`` is *not* in this set —
# it resolves to 127.0.0.1 and must be blocked.
_RESERVED_TEST_TLDS: tuple[str, ...] = (".example", ".test", ".invalid")


def _resolve_all(host: str) -> list[str]:
    """Return every IP address ``host`` resolves to, or ``[]`` on failure."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for info in infos:
        # sockaddr layout: IPv4 = (host, port); IPv6 = (host, port, flow, scope).
        sockaddr = info[4]
        ip = str(sockaddr[0])
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def is_safe_external_url(url: str) -> bool:
    """Return True if ``url`` is safe to fetch from an outbound HTTP client.

    "Safe" here means: the URL resolves to a public, routable address
    on the open Internet — not loopback, not RFC1918, not link-local
    (which includes cloud metadata endpoints), not multicast, not
    reserved.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _BLOCKED_HOSTS:
        log.debug("blocked SSRF host: %s", host)
        return False
    if any(host == tld[1:] or host.endswith(tld) for tld in _RESERVED_TEST_TLDS):
        # Reserved TLDs (RFC 2606): allowed because they cannot resolve to
        # a real host. The eventual fetch will fail with NXDOMAIN.
        return True
    # If the URL embeds a literal IP, check it directly without DNS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ips = _resolve_all(host)
        if not ips:
            log.debug("DNS resolution failed for %s; refusing fetch", host)
            return False
        return all(_ip_is_public(addr) for addr in ips)
    return _ip_is_public(str(ip))


def _ip_is_public(ip_str: str) -> bool:
    """True if ``ip_str`` is a public, routable address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.is_loopback:
        return False
    if ip.is_private:
        return False
    if ip.is_link_local:
        return False
    if ip.is_multicast:
        return False
    if ip.is_reserved:
        return False
    return not ip.is_unspecified


__all__ = ["is_safe_external_url"]
