"""Link verifier — HEAD with GET fallback. Runs across every URL referenced
in any artifact. The publish gate refuses to write outputs containing dead
links.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx

log = logging.getLogger(__name__)

Status = Literal["ok", "dead", "slow", "redirect", "error"]

DEFAULT_TIMEOUT = 6.0
DEFAULT_CONCURRENCY = 16
SLOW_THRESHOLD_SECONDS = 5.0
MAX_REDIRECTS = 3


async def _check_one(client: httpx.AsyncClient, url: str) -> tuple[str, Status, str]:
    """Return ``(url, status, reason)``. Never raises."""
    try:
        # HEAD first (cheaper). Some hosts 405 HEAD — fall back to GET.
        try:
            resp = await client.head(url, follow_redirects=True, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 405 or resp.status_code >= 500:
                resp = await client.get(url, follow_redirects=True, timeout=DEFAULT_TIMEOUT)
        except httpx.RequestError:
            resp = await client.get(url, follow_redirects=True, timeout=DEFAULT_TIMEOUT)

        elapsed = resp.elapsed.total_seconds() if resp.elapsed else 0.0
        if elapsed > SLOW_THRESHOLD_SECONDS:
            return url, "slow", f"{elapsed:.1f}s"
        if len(resp.history) > MAX_REDIRECTS:
            return url, "redirect", f"{len(resp.history)} hops"
        if resp.status_code >= 400:
            return url, "dead", str(resp.status_code)
        return url, "ok", str(resp.status_code)
    except httpx.HTTPError as exc:
        return url, "dead", repr(exc)[:80]
    except Exception as exc:
        return url, "error", repr(exc)[:80]


async def check_urls_async(
    urls: list[str],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, tuple[Status, str]]:
    """Concurrently HEAD/GET every URL. Returns ``{url: (status, reason)}``."""
    if not urls:
        return {}
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        headers={"User-Agent": "yc-ai-pulse link-verifier (+https://github.com/RyanAlberts/yc-ai-pulse)"},
        timeout=DEFAULT_TIMEOUT,
    ) as client:

        async def bounded(url: str) -> tuple[str, Status, str]:
            async with semaphore:
                return await _check_one(client, url)

        results = await asyncio.gather(*(bounded(url) for url in urls))

    return {url: (status, reason) for url, status, reason in results}


def check_urls(urls: list[str], concurrency: int = DEFAULT_CONCURRENCY) -> dict[str, tuple[Status, str]]:
    """Synchronous wrapper around :func:`check_urls_async`."""
    return asyncio.run(check_urls_async(urls, concurrency=concurrency))


def split_by_status(
    statuses: dict[str, tuple[Status, str]],
) -> dict[Status, list[tuple[str, str]]]:
    """Group results by status for reporting."""
    out: dict[Status, list[tuple[str, str]]] = {
        "ok": [],
        "dead": [],
        "slow": [],
        "redirect": [],
        "error": [],
    }
    for url, (status, reason) in statuses.items():
        out[status].append((url, reason))
    return out


__all__ = ["Status", "check_urls", "check_urls_async", "split_by_status"]
