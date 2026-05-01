"""Polite depth=1 website crawler for tech-stack and OSS-posture signal recovery.

PR #11 / B007. The LLM in v0.1 only sees the YC `long_description`, which rarely
mentions model providers, GitHub repos, license type, or pricing model. Result:
65/118 high-confidence W26 rows had ``oss_posture=unknown``. This module fetches
the company's homepage and a small set of high-signal subpages
(/about, /pricing, /docs, /security, /open-source, /github) so the LLM can cite
real evidence.

Politeness contract (non-negotiable):
- Honor robots.txt for the host.
- Identify ourselves with a User-Agent that links back to the project.
- Cap at 5 pages per company, 30 KB per page, 4-second timeout per fetch.
- Concurrent fetches per host capped at 2 to avoid hammering small startup hosts.
- Each page's HTML is stripped of <script>/<style> and sanitized for PII before
  it ever reaches the LLM or disk.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ycai.sanitizer import strip_pii

log = logging.getLogger(__name__)

USER_AGENT = "yc-ai-pulse research crawler (+https://github.com/RyanAlberts/yc-ai-pulse)"

DEFAULT_TIMEOUT = 4.0
DEFAULT_MAX_PAGES = 5
DEFAULT_PAGE_BYTES = 30_000
DEFAULT_PER_HOST_CONCURRENCY = 2

# URL path substrings ranked by signal density. Earlier entries get crawled
# first when we have to pick a subset of internal links.
_SIGNAL_PATHS: tuple[str, ...] = (
    "/pricing",
    "/security",
    "/about",
    "/docs",
    "/documentation",
    "/open-source",
    "/oss",
    "/license",
    "/github",
    "/api",
    "/developers",
    "/product",
    "/platform",
    "/technology",
    "/team",
    "/company",
    "/research",
    "/blog",
    "/changelog",
)

# Lifted out for testability — the smallest possible HTML link extractor.
# We deliberately don't pull in BeautifulSoup; the pipeline already has httpx
# + pydantic, and an HTML parser is one more thing to break and audit.
_LINK_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CrawledPage:
    """One fetched page. ``text`` is HTML-stripped, sanitized, ready for LLM."""

    url: str
    status: int
    text: str  # post-sanitization
    bytes_fetched: int


@dataclass(frozen=True)
class CrawlResult:
    """Per-company crawl outcome."""

    homepage: str
    pages: list[CrawledPage] = field(default_factory=list)
    robots_blocked: bool = False
    error: str | None = None  # set if the homepage itself was unreachable

    @property
    def cited_urls(self) -> list[str]:
        """URLs the model is allowed to cite as evidence."""
        return [p.url for p in self.pages if p.status < 400]

    @property
    def total_text_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


def _extract_text(html: str) -> str:
    """Strip script/style/tags, collapse whitespace. Lossy by design."""
    cleaned = _SCRIPT_RE.sub(" ", html)
    cleaned = _STYLE_RE.sub(" ", cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def _extract_links(html: str, base_url: str) -> list[str]:
    """Return absolute URLs found in <a href> attributes, deduped, same-host only."""
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[str] = []
    for m in _LINK_RE.finditer(html):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        # Same host only — never wander off-site, even to a github.com link
        # (we still mention github.com presence by reading the homepage; we
        # just don't fetch off-host pages here for politeness).
        if parsed.netloc != base_host:
            continue
        absolute = absolute.split("#", 1)[0]
        if absolute in seen or absolute == base_url:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def _rank_links(urls: list[str]) -> list[str]:
    """Sort by signal-path priority. URLs containing earlier _SIGNAL_PATHS rank higher.
    Unknown paths sort last."""

    def score(url: str) -> tuple[int, int]:
        path = urlparse(url).path.lower()
        for i, needle in enumerate(_SIGNAL_PATHS):
            if needle in path:
                return (i, len(path))
        return (len(_SIGNAL_PATHS), len(path))

    return sorted(urls, key=score)


async def _check_robots(client: httpx.AsyncClient, host_url: str) -> RobotFileParser | None:
    """Fetch and parse robots.txt for the host. None on failure (treat as allowed)."""
    parsed = urlparse(host_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        log.debug("robots.txt fetch failed for %s: %s", robots_url, exc)
        return None
    if resp.status_code >= 400:
        return None
    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    *,
    semaphore: asyncio.Semaphore,
    max_bytes: int,
    timeout: float,
) -> CrawledPage | None:
    """Fetch one page. Return None on any error."""
    async with semaphore:
        try:
            async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    return CrawledPage(url=url, status=resp.status_code, text="", bytes_fetched=0)
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type and "xml" not in content_type:
                    # Skip PDFs, JSON APIs, etc. — text-only crawl.
                    return None
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= max_bytes:
                        break
                html = bytes(buf[:max_bytes]).decode("utf-8", errors="replace")
                text = strip_pii(_extract_text(html))
                return CrawledPage(
                    url=str(resp.url),
                    status=resp.status_code,
                    text=text,
                    bytes_fetched=len(buf),
                )
        except httpx.HTTPError as exc:
            log.debug("fetch failed for %s: %s", url, exc)
            return None
        except Exception as exc:
            log.warning("crawler unexpected failure on %s: %s", url, exc.__class__.__name__)
            return None


async def crawl_company(
    homepage: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_bytes: int = DEFAULT_PAGE_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    per_host_concurrency: int = DEFAULT_PER_HOST_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> CrawlResult:
    """Crawl up to ``max_pages`` from ``homepage`` at depth=1.

    Returns a :class:`CrawlResult`. Always returns — never raises — so the
    pipeline can recover gracefully when a company's site is down.
    """
    if not homepage or not homepage.startswith(("http://", "https://")):
        return CrawlResult(homepage=homepage, error="invalid-homepage-url")

    owns_client = client is None
    client = client or httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    semaphore = asyncio.Semaphore(per_host_concurrency)

    try:
        # Politeness: check robots.txt before the homepage fetch.
        robots = await _check_robots(client, homepage)
        if robots is not None and not robots.can_fetch(USER_AGENT, homepage):
            return CrawlResult(homepage=homepage, robots_blocked=True)

        # Fetch homepage first; we need its HTML to discover internal links.
        home_page = await _fetch_one(client, homepage, semaphore=semaphore, max_bytes=max_bytes, timeout=timeout)
        if home_page is None or home_page.status >= 400:
            return CrawlResult(
                homepage=homepage,
                pages=[home_page] if home_page else [],
                error="homepage-unreachable",
            )

        pages: list[CrawledPage] = [home_page]

        # Discover candidate internal links from the homepage HTML. We need the
        # raw HTML for link extraction — re-fetch in a moment if not preserved.
        # For efficiency we just refetch the homepage's HTML body here, since
        # _fetch_one already stripped to text. Cleaner: change _fetch_one to
        # return raw HTML too. For now keep the contract simple.
        try:
            raw = await client.get(homepage, timeout=timeout)
            internal = _extract_links(raw.text, str(raw.url)) if raw.status_code < 400 else []
        except httpx.HTTPError:
            internal = []
        ranked = _rank_links(internal)

        budget = max_pages - 1  # already used one for the homepage
        candidates = ranked[: budget * 2]  # 2x to allow some failures
        # Honor robots.txt on each candidate.
        if robots is not None:
            candidates = [u for u in candidates if robots.can_fetch(USER_AGENT, u)]

        # Fetch concurrently, but capped.
        coros = [
            _fetch_one(client, url, semaphore=semaphore, max_bytes=max_bytes, timeout=timeout)
            for url in candidates[:budget]
        ]
        if coros:
            for page in await asyncio.gather(*coros):
                if page is not None and page.status < 400:
                    pages.append(page)
                if len(pages) >= max_pages:
                    break

        return CrawlResult(homepage=homepage, pages=pages)
    finally:
        if owns_client:
            await client.aclose()


async def crawl_companies(
    homepages: list[str],
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_bytes: int = DEFAULT_PAGE_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    overall_concurrency: int = 8,
) -> dict[str, CrawlResult]:
    """Crawl many companies. ``overall_concurrency`` caps total in-flight crawls."""
    semaphore = asyncio.Semaphore(overall_concurrency)

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    ) as client:

        async def one(url: str) -> tuple[str, CrawlResult]:
            async with semaphore:
                return url, await crawl_company(
                    url,
                    max_pages=max_pages,
                    max_bytes=max_bytes,
                    timeout=timeout,
                    client=client,
                )

        results = await asyncio.gather(*(one(u) for u in homepages))
    return dict(results)


__all__ = [
    "USER_AGENT",
    "CrawlResult",
    "CrawledPage",
    "crawl_companies",
    "crawl_company",
]
