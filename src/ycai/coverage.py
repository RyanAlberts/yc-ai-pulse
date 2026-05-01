"""Coverage probe — classify each company by data quality and produce the
dropped register.

The headline number on every dashboard / deck / memo is "% of YC batch covered".
Coverage = (Tier A + Tier B) / total. Tier C companies are listed by name with
the specific reason they were excluded — no quiet drops.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from ycai.schemas import (
    BatchCoverage,
    CoverageRecord,
    CoverageTier,
    DropReason,
    RawCompany,
)

log = logging.getLogger(__name__)

# Threshold tuning — exposed as constants so they're auditable.
MIN_DESCRIPTION_CHARS = 80
MIN_ONE_LINER_CHARS = 10


def _required_field_drops(company: RawCompany) -> list[DropReason]:
    drops: list[DropReason] = []
    if not company.slug or " " in company.slug:
        drops.append(DropReason.INVALID_SLUG)
    if not company.website or not company.website.startswith(("http://", "https://")):
        drops.append(DropReason.NO_WEBSITE)
    if len(company.long_description) < MIN_DESCRIPTION_CHARS:
        drops.append(DropReason.NO_DESCRIPTION)
    if not company.industries and not company.industry:
        drops.append(DropReason.NO_INDUSTRY)
    if company.status and company.status.lower() in {"inactive", "dead", "closed"}:
        drops.append(DropReason.INACTIVE)
    return drops


def classify_company(
    company: RawCompany,
    website_status: str | None = None,
) -> CoverageRecord:
    """Tier-classify a single company.

    ``website_status`` is the verifier's verdict if available:
      ``ok`` (2xx/3xx), ``dead`` (4xx/5xx), ``slow`` (>5s), ``redirect`` (>3 hops),
      ``None`` if the verifier hasn't been run yet (Tier B is still possible
      from missing/empty website).
    """
    drops = _required_field_drops(company)

    # Tier C: any required field missing.
    if drops:
        return CoverageRecord(
            slug=company.slug,
            name=company.name,
            tier=CoverageTier.C,
            drop_reasons=drops,
            website_status=website_status,
        )

    notes: list[str] = []
    # Tier B: required fields present, website unreachable.
    if website_status == "dead":
        notes.append("website returned 4xx/5xx — analysis flagged")
        return CoverageRecord(
            slug=company.slug,
            name=company.name,
            tier=CoverageTier.B,
            drop_reasons=[],
            website_status=website_status,
            notes=notes,
        )

    if website_status in {"slow", "redirect"}:
        notes.append(f"website returned status={website_status} — keep but flag")

    # Tier A.
    return CoverageRecord(
        slug=company.slug,
        name=company.name,
        tier=CoverageTier.A,
        drop_reasons=[],
        website_status=website_status,
        notes=notes,
    )


def compute_coverage(
    companies: Iterable[RawCompany],
    batch_slug: str,
    batch_label: str,
    upstream_count: int,
    yc_official_count: int | None = None,
    website_statuses: Mapping[str, str] | None = None,
    source: str = "yc-oss/api",
    source_last_updated: datetime | None = None,
) -> BatchCoverage:
    """Run the coverage probe over a batch and produce the aggregate.

    Args:
        companies: Output of :func:`scraper.fetch_batch`.
        batch_slug, batch_label: Batch identity.
        upstream_count: Companies the upstream source listed.
        yc_official_count: Companies in the actual YC batch (from a trusted
            external reference like a Demo Day report). When set, the
            coverage % vs. official count is rendered as the headline metric.
        website_statuses: Map of slug -> verifier verdict ('ok', 'dead', ...).
            Pass ``{}`` if the verifier hasn't run yet.
        source: Identifier shown in the methodology footer.
    """
    statuses = website_statuses or {}
    records: list[CoverageRecord] = []
    seen_slugs: set[str] = set()

    for company in companies:
        if company.slug in seen_slugs:
            records.append(
                CoverageRecord(
                    slug=company.slug,
                    name=company.name,
                    tier=CoverageTier.C,
                    drop_reasons=[DropReason.DUPLICATE],
                )
            )
            continue
        seen_slugs.add(company.slug)
        records.append(classify_company(company, website_status=statuses.get(company.slug)))

    tier_counts = {CoverageTier.A: 0, CoverageTier.B: 0, CoverageTier.C: 0}
    for record in records:
        tier_counts[record.tier] += 1

    return BatchCoverage(
        batch_slug=batch_slug,
        batch_label=batch_label,
        source=source,
        source_last_updated=source_last_updated,
        fetched_at=datetime.now(UTC),
        upstream_company_count=upstream_count,
        yc_official_count=yc_official_count,
        tier_a_count=tier_counts[CoverageTier.A],
        tier_b_count=tier_counts[CoverageTier.B],
        tier_c_count=tier_counts[CoverageTier.C],
        records=records,
    )


def coverage_summary_lines(coverage: BatchCoverage) -> list[str]:
    """Plain-text summary suitable for stdout, dashboards, deck methodology slides."""
    lines = [
        f"Batch: {coverage.batch_label} (slug={coverage.batch_slug})",
        f"Source: {coverage.source}, last_updated={coverage.source_last_updated}",
        f"Upstream count: {coverage.upstream_company_count}",
    ]
    if coverage.yc_official_count is not None:
        lines.append(f"YC official count: {coverage.yc_official_count}")
        official_gap = coverage.yc_official_count - coverage.upstream_company_count
        if official_gap > 0:
            pct = round(100.0 * coverage.upstream_company_count / coverage.yc_official_count, 1)
            lines.append(
                f"  ⚠ upstream is missing {official_gap} of {coverage.yc_official_count} "
                f"({pct}% present). Likely upstream staleness."
            )
    lines.extend(
        [
            f"Tier A (full):     {coverage.tier_a_count}",
            f"Tier B (partial):  {coverage.tier_b_count}",
            f"Tier C (excluded): {coverage.tier_c_count}",
            f"Coverage of upstream: {coverage.coverage_pct_of_upstream}%",
        ]
    )
    if coverage.coverage_pct_of_official is not None:
        lines.append(f"Coverage of YC official:  {coverage.coverage_pct_of_official}%  ← headline")
    return lines


def dropped_register(coverage: BatchCoverage) -> list[CoverageRecord]:
    """Return the Tier-C companies, sorted by slug, for transparent reporting."""
    return sorted(
        (r for r in coverage.records if r.tier == CoverageTier.C),
        key=lambda r: r.slug,
    )


__all__ = [
    "MIN_DESCRIPTION_CHARS",
    "MIN_ONE_LINER_CHARS",
    "classify_company",
    "compute_coverage",
    "coverage_summary_lines",
    "dropped_register",
]
