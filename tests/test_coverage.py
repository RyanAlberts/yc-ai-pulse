"""Tests for ycai.coverage — tier classification and dropped register."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ycai.coverage import (
    MIN_DESCRIPTION_CHARS,
    classify_company,
    compute_coverage,
    coverage_summary_lines,
)
from ycai.schemas import CoverageTier, DropReason, RawCompany


def _make_company(**overrides: object) -> RawCompany:
    base = {
        "slug": "acme-ai",
        "name": "Acme AI",
        "batch": "Winter 2026",
        "website": "https://acme.ai",
        "one_liner": "AI for clouds",
        "long_description": "x" * (MIN_DESCRIPTION_CHARS + 20),
        "industry": "B2B",
        "industries": ["B2B"],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return RawCompany.model_validate(base)


def test_tier_a_company_with_ok_website() -> None:
    record = classify_company(_make_company(), website_status="ok")
    assert record.tier == CoverageTier.A
    assert record.drop_reasons == []


def test_tier_b_company_with_dead_website() -> None:
    record = classify_company(_make_company(), website_status="dead")
    assert record.tier == CoverageTier.B
    assert record.notes  # has the "flagged" note


def test_tier_c_no_website() -> None:
    record = classify_company(_make_company(website=""))
    assert record.tier == CoverageTier.C
    assert DropReason.NO_WEBSITE in record.drop_reasons


def test_tier_c_no_description() -> None:
    record = classify_company(_make_company(long_description=""))
    assert record.tier == CoverageTier.C
    assert DropReason.NO_DESCRIPTION in record.drop_reasons


def test_tier_c_no_industry() -> None:
    record = classify_company(_make_company(industry="", industries=[]))
    assert record.tier == CoverageTier.C
    assert DropReason.NO_INDUSTRY in record.drop_reasons


def test_tier_c_invalid_slug_with_space() -> None:
    record = classify_company(_make_company(slug="bad slug"))
    assert record.tier == CoverageTier.C
    assert DropReason.INVALID_SLUG in record.drop_reasons


def test_tier_c_inactive_company() -> None:
    record = classify_company(_make_company(status="Inactive"))
    assert record.tier == CoverageTier.C
    assert DropReason.INACTIVE in record.drop_reasons


def test_tier_c_multiple_reasons_collected() -> None:
    record = classify_company(_make_company(website="", long_description=""))
    assert record.tier == CoverageTier.C
    assert DropReason.NO_WEBSITE in record.drop_reasons
    assert DropReason.NO_DESCRIPTION in record.drop_reasons


@pytest.mark.parametrize("invalid_protocol", ["", "ftp://x", "javascript:alert(1)", "//example.com"])
def test_website_must_be_http_or_https(invalid_protocol: str) -> None:
    record = classify_company(_make_company(website=invalid_protocol))
    assert record.tier == CoverageTier.C
    assert DropReason.NO_WEBSITE in record.drop_reasons


def test_compute_coverage_rolls_up_counts() -> None:
    companies = [
        _make_company(slug="ok-1"),
        _make_company(slug="ok-2"),
        _make_company(slug="dead-1"),
        _make_company(slug="bad-1", long_description=""),
    ]
    statuses = {"ok-1": "ok", "ok-2": "ok", "dead-1": "dead", "bad-1": "ok"}
    coverage = compute_coverage(
        companies=companies,
        batch_slug="winter-2026",
        batch_label="Winter 2026",
        upstream_count=4,
        yc_official_count=10,
        website_statuses=statuses,
    )
    assert coverage.tier_a_count == 2
    assert coverage.tier_b_count == 1
    assert coverage.tier_c_count == 1
    assert coverage.analyzable_count == 3
    assert coverage.coverage_pct_of_upstream == 75.0
    assert coverage.coverage_pct_of_official == 30.0


def test_compute_coverage_dedupes_repeated_slugs() -> None:
    companies = [_make_company(slug="dup"), _make_company(slug="dup")]
    coverage = compute_coverage(
        companies=companies,
        batch_slug="winter-2026",
        batch_label="Winter 2026",
        upstream_count=2,
    )
    # First passes; second is flagged duplicate.
    assert coverage.tier_a_count == 1
    assert coverage.tier_c_count == 1
    dup = next(r for r in coverage.records if r.tier == CoverageTier.C)
    assert DropReason.DUPLICATE in dup.drop_reasons


def test_summary_lines_includes_official_count_when_present() -> None:
    coverage = compute_coverage(
        companies=[_make_company()],
        batch_slug="winter-2026",
        batch_label="Winter 2026",
        upstream_count=1,
        yc_official_count=10,
        website_statuses={"acme-ai": "ok"},
        source_last_updated=datetime(2026, 2, 8, tzinfo=UTC),
    )
    lines = coverage_summary_lines(coverage)
    joined = "\n".join(lines)
    assert "YC official count" in joined
    assert "headline" in joined


def test_summary_lines_omits_official_when_unknown() -> None:
    coverage = compute_coverage(
        companies=[_make_company()],
        batch_slug="winter-2026",
        batch_label="Winter 2026",
        upstream_count=1,
        website_statuses={"acme-ai": "ok"},
    )
    lines = coverage_summary_lines(coverage)
    assert all("official" not in line.lower() for line in lines)
