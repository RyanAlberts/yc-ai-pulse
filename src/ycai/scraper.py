"""Fetch the latest YC batch from yc-oss/api.

Per ADR 0001: yc-oss/api is the only sanctioned source for the batch listing.
The disallowed `ycombinator.com/companies?batch=...` URL is never used.

If yc-oss/api is unreachable or stale, this module fails loudly. The caller
should not silently fall back to scraping.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ycai.sanitizer import sanitize_dict
from ycai.schemas import RawCompany

log = logging.getLogger(__name__)

YC_OSS_META = "https://yc-oss.github.io/api/meta.json"
YC_OSS_BATCH_TEMPLATE = "https://yc-oss.github.io/api/batches/{slug}.json"
DEFAULT_TIMEOUT = 30.0


class UpstreamError(RuntimeError):
    """yc-oss/api is unreachable, malformed, or missing the requested batch."""


@dataclass(frozen=True)
class BatchMeta:
    """Metadata about the batch we're about to fetch."""

    slug: str  # 'winter-2026'
    label: str  # 'Winter 2026'
    upstream_count: int
    api_url: str
    source_last_updated: datetime | None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def fetch_meta(client: httpx.Client | None = None) -> dict:
    """Pull the yc-oss meta JSON. Raises ``UpstreamError`` on failure."""
    owns_client = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    try:
        try:
            resp = client.get(YC_OSS_META)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"yc-oss meta unreachable: {exc!r}") from exc
        return resp.json()
    finally:
        if owns_client:
            client.close()


def detect_latest_batch(meta: dict | None = None) -> BatchMeta:
    """Pick the most-recent batch from the yc-oss meta JSON.

    yc-oss naming: ``winter-YYYY`` / ``summer-YYYY`` / ``fall-YYYY`` / ``spring-YYYY``.
    We sort by (year, season-rank) where rank is winter=0, spring=1, summer=2, fall=3.
    """
    meta = meta or fetch_meta()
    batches: dict[str, dict] = meta.get("batches", {})
    if not batches:
        raise UpstreamError("yc-oss meta has no batches")

    season_rank = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}

    def sort_key(slug: str) -> tuple[int, int]:
        try:
            season, year = slug.split("-")
            return (int(year), season_rank.get(season.lower(), 99))
        except ValueError:
            return (-1, -1)

    latest_slug = max(batches.keys(), key=sort_key)
    entry = batches[latest_slug]
    label = " ".join(p.capitalize() for p in latest_slug.split("-"))
    return BatchMeta(
        slug=latest_slug,
        label=label,
        upstream_count=int(entry.get("count", 0)),
        api_url=entry.get("api", YC_OSS_BATCH_TEMPLATE.format(slug=latest_slug)),
        source_last_updated=_parse_iso(meta.get("last_updated")),
    )


def fetch_batch(
    slug: str | None = None,
    client: httpx.Client | None = None,
    cache_dir: Path | None = None,
) -> tuple[BatchMeta, list[RawCompany]]:
    """Fetch a YC batch from yc-oss/api.

    Args:
        slug: Batch slug (e.g. ``winter-2026``). ``None`` autodetects the latest.
        client: Optional httpx client (for tests / shared connection pool).
        cache_dir: If set, writes raw JSON to ``<cache_dir>/raw/yc_companies.json``
            (sanitized). Re-runs are not yet skipped — caller decides.

    Returns:
        ``(BatchMeta, list[RawCompany])``.

    Raises:
        UpstreamError: yc-oss is unreachable, the batch slug is missing, or the
            payload is malformed.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    try:
        meta = fetch_meta(client=client)
        if slug is None:
            batch_meta = detect_latest_batch(meta=meta)
        else:
            entry = meta.get("batches", {}).get(slug)
            if not entry:
                raise UpstreamError(f"yc-oss meta does not list batch slug {slug!r}")
            label = " ".join(p.capitalize() for p in slug.split("-"))
            batch_meta = BatchMeta(
                slug=slug,
                label=label,
                upstream_count=int(entry.get("count", 0)),
                api_url=entry.get("api", YC_OSS_BATCH_TEMPLATE.format(slug=slug)),
                source_last_updated=_parse_iso(meta.get("last_updated")),
            )

        log.info(
            "fetching batch %s from %s (upstream count=%s, last_updated=%s)",
            batch_meta.slug,
            batch_meta.api_url,
            batch_meta.upstream_count,
            batch_meta.source_last_updated,
        )

        try:
            resp = client.get(batch_meta.api_url)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"yc-oss batch {batch_meta.slug} unreachable: {exc!r}") from exc
        except json.JSONDecodeError as exc:
            raise UpstreamError(f"yc-oss batch {batch_meta.slug} malformed JSON: {exc!r}") from exc

        if not isinstance(payload, list):
            raise UpstreamError(f"yc-oss batch {batch_meta.slug} expected list, got {type(payload).__name__}")

        # Sanitize before any data hits disk or downstream code.
        sanitized = [sanitize_dict(c) for c in payload]
        companies = [RawCompany.model_validate(c) for c in sanitized]

        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            raw_path = cache_dir / "yc_companies.json"
            raw_path.write_text(json.dumps(sanitized, indent=2, default=str))
            log.info("wrote sanitized cache to %s", raw_path)

        return batch_meta, companies
    finally:
        if owns_client:
            client.close()


def upstream_age_hours(meta_last_updated: datetime | None, now: datetime | None = None) -> float | None:
    """Hours since yc-oss/api's last update. ``None`` if unknown."""
    if meta_last_updated is None:
        return None
    now = now or datetime.now(UTC)
    if meta_last_updated.tzinfo is None:
        meta_last_updated = meta_last_updated.replace(tzinfo=UTC)
    delta = now - meta_last_updated
    return round(delta.total_seconds() / 3600.0, 1)


__all__ = [
    "BatchMeta",
    "UpstreamError",
    "detect_latest_batch",
    "fetch_batch",
    "fetch_meta",
    "upstream_age_hours",
]
