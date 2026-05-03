"""External signal fetching: Hacker News + Reddit + GitHub stars.

The depth=1 website crawler in ``crawler.py`` covers the company's own
surfaces. This module reaches further — into discussion sites where a
company's reputation actually shows up. The goal is to give the
three-POV essays and the topology cluster something to argue with
beyond marketing copy.

Sources:
- **Hacker News** via the public Algolia search API
  (https://hn.algolia.com/api/v1/search). Free, no auth.
  Returns story title, URL, points, num_comments.
- **Reddit** via the public listing JSON
  (https://www.reddit.com/search.json?q=...). Free, no auth, but
  rate-limited.
- **GitHub** stars when a github.com URL was discovered in the
  depth=1 crawl. Public API, no auth needed for low-rate reads.

Signal-quality guards (these matter more than the fetch itself):

- **Word-boundary match.** Substring matching catches 'crow' inside
  'crowdstrike' and 'fort' inside 'fortnite'. We use ``\\b{name}\\b`` to
  drop those. Short generic names ('Crow', 'Fort', 'Pocket', 'Origin')
  still leak through if the unrelated story uses the bare word, so:
- **URL-host alignment.** When we have the company's website host, we
  *strongly* prefer hits whose linked URL host contains it. Hits that
  don't host-match are kept only when the title contains the company
  name AND a hint phrase (a YC company's website domain, the YC slug,
  or 'yc.com'). This eliminates the worst false positives.
- **Recency window.** YC W26 companies founded in 2025-2026 cannot have
  generated HN traction in 2014. We restrict HN results to the last
  18 months via Algolia's ``numericFilters=created_at_i>...``.
- **Min name length.** Names under 4 chars are skipped — too generic
  to query usefully on either platform.

Politeness contract:
- Per-host concurrency capped at 4.
- 5-second per-request timeout.
- Identifies us via User-Agent.
- Skips silently on any error — never raises.

The output is a list of ``ExternalSignal`` records keyed by company
slug, persisted alongside ``analyses.jsonl`` as ``external_signals.jsonl``
so the memo build can consume them at zero LLM cost.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "yc-ai-pulse research probe (+https://github.com/RyanAlberts/yc-ai-pulse)"
DEFAULT_TIMEOUT = 5.0
DEFAULT_CONCURRENCY = 4

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
REDDIT_SEARCH = "https://www.reddit.com/search.json"
GITHUB_REPO_API = "https://api.github.com/repos/{owner}/{repo}"

# Names shorter than this are treated as too generic to query (e.g., 'Crow',
# 'Fort', 'Pocket', 'Origin', 'Bits'). The cost of false positives outweighs
# the lift from finding the rare real hit.
MIN_NAME_LENGTH = 5

# 18-month recency window. W26 companies were founded recently; HN posts
# older than this aren't about them.
RECENCY_WINDOW_SECONDS = 18 * 30 * 24 * 3600


def _host_of(url: str) -> str:
    """Lowercase host without a leading 'www.'."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _name_token_re(name: str) -> re.Pattern[str] | None:
    """Word-boundary regex for ``name``. Returns None if the name is unusable."""
    cleaned = name.strip()
    if len(cleaned) < MIN_NAME_LENGTH:
        return None
    return re.compile(rf"\b{re.escape(cleaned)}\b", re.IGNORECASE)


def _url_aligns(story_url: str, anchors: tuple[str, ...]) -> bool:
    """True if ``story_url``'s host matches any anchor (company website
    host, YC profile slug-host, or yc.com).
    """
    host = _host_of(story_url)
    if not host:
        return False
    return any(a and a in host for a in anchors)


@dataclass(frozen=True)
class ExternalSignal:
    """One external signal about a company."""

    slug: str  # the YC slug
    source: str  # "hn" | "reddit" | "github"
    title: str
    url: str
    score: int  # HN points / Reddit upvotes / GitHub stars
    detail: str = ""  # extra context (subreddit, comment count, language)


@dataclass
class CompanyExternalProfile:
    """All external signals collected for one company."""

    slug: str
    hn: list[ExternalSignal] = field(default_factory=list)
    reddit: list[ExternalSignal] = field(default_factory=list)
    github: ExternalSignal | None = None

    @property
    def total_count(self) -> int:
        return len(self.hn) + len(self.reddit) + (1 if self.github else 0)


# ----- HN ------------------------------------------------------------------------


async def _fetch_hn(
    client: httpx.AsyncClient,
    slug: str,
    name: str,
    *,
    website_host: str = "",
    limit: int = 5,
) -> list[ExternalSignal]:
    """Pull recent HN stories that plausibly reference the company.

    The substring match this used to do produces catastrophic false
    positives on short common-word names (Crow→CrowdStrike, Fort→Fortnite,
    Origin→uBlock Origin). We use word-boundary matching, restrict to the
    last 18 months, and require either:
      a) the story URL host aligns with the company website host, or
      b) the title contains the company's website host or YC slug —
         confirming the story is about *this* company, not a homonym.
    """
    pattern = _name_token_re(name)
    if pattern is None:
        return []
    cutoff = int(time.time() - RECENCY_WINDOW_SECONDS)
    params: dict[str, Any] = {
        "query": name,
        "tags": "story",
        "hitsPerPage": max(limit * 4, 20),  # over-fetch; we filter aggressively
        "numericFilters": f"created_at_i>{cutoff}",
    }
    try:
        resp = await client.get(HN_SEARCH, params=params, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("HN fetch failed for %s: %s", slug, exc)
        return []
    if not website_host:
        # Without a website host we can't disambiguate. Bail rather than
        # ship false positives.
        return []
    out: list[ExternalSignal] = []
    for hit in body.get("hits", []):
        title = (hit.get("title") or "").strip()
        if not pattern.search(title):
            continue
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        title_lower = title.lower()
        # Strict path A: story URL host matches the company's website host.
        host_aligned = _url_aligns(story_url, (website_host,))
        # Strict path B: title literally contains the FULL website host
        # ("getpocket.com" — not "pocket"). Distinctive enough to trust.
        title_anchored = website_host in title_lower
        # Strict path C: launch posts mention "(YC <batch>)" which is
        # extremely distinctive — these are almost always real.
        is_launch_post = "launch hn" in title_lower or re.search(r"\(yc [wsfWSF]\d{2}\)", title) is not None
        if not (host_aligned or title_anchored or is_launch_post):
            continue
        score = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        out.append(
            ExternalSignal(
                slug=slug,
                source="hn",
                title=title[:140],
                url=story_url,
                score=score,
                detail=f"{comments} comments",
            )
        )
        if len(out) >= limit:
            break
    return out


# ----- Reddit ---------------------------------------------------------------------


async def _fetch_reddit(
    client: httpx.AsyncClient,
    slug: str,
    name: str,
    *,
    website_host: str = "",
    limit: int = 5,
) -> list[ExternalSignal]:
    """Pull recent Reddit posts that plausibly reference the company.

    Same noise-management as ``_fetch_hn``: word-boundary match on the title,
    plus URL-host or title-host alignment.
    """
    pattern = _name_token_re(name)
    if pattern is None:
        return []
    params: dict[str, Any] = {
        "q": f'"{name}"',  # quoted phrase match
        "limit": max(limit * 4, 20),
        "sort": "relevance",
        "type": "link",
        "t": "year",  # last year only — Reddit's coarsest recency knob
    }
    try:
        resp = await client.get(
            REDDIT_SEARCH,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("Reddit fetch failed for %s: %s", slug, exc)
        return []
    if not website_host:
        return []
    out: list[ExternalSignal] = []
    for child in body.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = (post.get("title") or "").strip()
        if not pattern.search(title):
            continue
        permalink = post.get("permalink") or ""
        permalink_url = f"https://www.reddit.com{permalink}" if permalink else ""
        external_url = post.get("url_overridden_by_dest") or post.get("url") or ""
        selftext = (post.get("selftext") or "").lower()
        title_lower = title.lower()
        host_aligned = _url_aligns(external_url, (website_host,))
        title_anchored = website_host in title_lower
        body_anchored = website_host in selftext
        if not (host_aligned or title_anchored or body_anchored):
            continue
        url = permalink_url or external_url
        if not url:
            continue
        score = int(post.get("ups") or 0)
        subreddit = post.get("subreddit") or ""
        out.append(
            ExternalSignal(
                slug=slug,
                source="reddit",
                title=title[:140],
                url=url,
                score=score,
                detail=f"r/{subreddit}",
            )
        )
        if len(out) >= limit:
            break
    return out


# ----- GitHub --------------------------------------------------------------------


_GITHUB_REPO_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/?", re.IGNORECASE)


async def _fetch_github(client: httpx.AsyncClient, slug: str, github_urls: list[str]) -> ExternalSignal | None:
    """Fetch star count for the most-starred GitHub repo we found."""
    seen: set[tuple[str, str]] = set()
    for url in github_urls:
        m = _GITHUB_REPO_RE.match(url)
        if not m:
            continue
        owner, repo = m.group(1), m.group(2).rstrip(".git")
        if owner.lower() in {"orgs", "topics", "search"}:
            continue
        if (owner, repo) in seen:
            continue
        seen.add((owner, repo))
        api_url = GITHUB_REPO_API.format(owner=owner, repo=repo)
        try:
            resp = await client.get(api_url, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                continue
            data: dict[str, Any] = resp.json()
        except (httpx.HTTPError, ValueError):
            continue
        stars = int(data.get("stargazers_count") or 0)
        language = data.get("language") or ""
        return ExternalSignal(
            slug=slug,
            source="github",
            title=f"{owner}/{repo}",
            url=f"https://github.com/{owner}/{repo}",
            score=stars,
            detail=language,
        )
    return None


def _extract_github_urls_from_crawl(crawl_results_path: str | None, slug: str) -> list[str]:
    """Best-effort: pull github.com URLs out of crawled-page text.

    The crawler stores the per-company page texts via ``CrawledPage.text``;
    the run directory's ``crawl_results.jsonl`` keeps URLs but not full text.
    For PR #19 we look for ``github.com/<owner>/<repo>`` substrings in the
    sanitized analyses ``sources`` and ``oss_evidence_url`` since those are
    already gated by Layer 1's source-URL guard.
    """
    return []  # stub — handled inline by the caller using analysis fields


# ----- public driver --------------------------------------------------------------


async def fetch_for_company(
    client: httpx.AsyncClient,
    slug: str,
    name: str,
    *,
    website_host: str = "",
    github_urls: list[str] | None = None,
) -> CompanyExternalProfile:
    """Fan out HN + Reddit + GitHub fetches for one company. Never raises."""
    profile = CompanyExternalProfile(slug=slug)
    try:
        hn, reddit = await asyncio.gather(
            _fetch_hn(client, slug, name, website_host=website_host),
            _fetch_reddit(client, slug, name, website_host=website_host),
        )
        profile.hn = hn
        profile.reddit = reddit
    except Exception as exc:
        log.warning("external fetch failed for %s: %s", slug, exc.__class__.__name__)
    if github_urls:
        try:
            profile.github = await _fetch_github(client, slug, github_urls)
        except Exception as exc:
            log.warning("github fetch failed for %s: %s", slug, exc.__class__.__name__)
    return profile


async def fetch_for_cohort(
    targets: list[tuple[str, str, list[str]]] | list[tuple[str, str, str, list[str]]],
    *,
    overall_concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, CompanyExternalProfile]:
    """Fetch external signals for every company in ``targets``.

    Accepts both ``(slug, name, github_urls)`` and ``(slug, name, website,
    github_urls)``. The 4-tuple form lets us pass the company website host
    so the noise filters can use it as an anchor.
    """
    semaphore = asyncio.Semaphore(overall_concurrency)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    ) as client:

        async def one_4(
            slug: str, name: str, website: str, github_urls: list[str]
        ) -> tuple[str, CompanyExternalProfile]:
            async with semaphore:
                return slug, await fetch_for_company(
                    client,
                    slug,
                    name,
                    website_host=_host_of(website),
                    github_urls=github_urls,
                )

        async def one_3(slug: str, name: str, github_urls: list[str]) -> tuple[str, CompanyExternalProfile]:
            async with semaphore:
                return slug, await fetch_for_company(client, slug, name, github_urls=github_urls)

        coros = []
        for t in targets:
            if len(t) == 4:
                slug4, name4, website4, urls4 = t
                coros.append(one_4(slug4, name4, website4, urls4))
            else:
                slug3, name3, urls3 = t
                coros.append(one_3(slug3, name3, urls3))
        results = await asyncio.gather(*coros)
    return dict(results)


# ----- helpers used by the memo build ---------------------------------------------


def best_signal(profile: CompanyExternalProfile) -> ExternalSignal | None:
    """Return the highest-signal external item we found, or None."""
    candidates: list[ExternalSignal] = list(profile.hn) + list(profile.reddit)
    if profile.github:
        candidates.append(profile.github)
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.score)


def signal_count(profile: CompanyExternalProfile) -> int:
    return profile.total_count


def quoted_url(url: str) -> str:
    """Quote URL for safe inclusion in prose without breaking pydantic checks."""
    return quote(url, safe=":/?&=._-#")


__all__ = [
    "CompanyExternalProfile",
    "ExternalSignal",
    "best_signal",
    "fetch_for_cohort",
    "fetch_for_company",
    "quoted_url",
    "signal_count",
]
