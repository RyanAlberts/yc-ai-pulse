"""Tests for dashboard rendering and the cited-URL publish gate."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ycai.dashboard import collect_cited_urls, render, write_broken_links_report
from ycai.schemas import (
    AICapability,
    BatchCoverage,
    CompanyAnalysis,
    CoverageRecord,
    CoverageTier,
    DropReason,
    Industry,
    OSSPosture,
    RawCompany,
    TechStack,
)


def _make_coverage(tier_a: int = 2, tier_b: int = 0, tier_c: int = 1, official: int | None = 5) -> BatchCoverage:
    records = (
        [CoverageRecord(slug=f"a{i}", name=f"Acme {i}", tier=CoverageTier.A) for i in range(tier_a)]
        + [CoverageRecord(slug=f"b{i}", name=f"Beta {i}", tier=CoverageTier.B) for i in range(tier_b)]
        + [
            CoverageRecord(
                slug=f"c{i}",
                name=f"Gamma {i}",
                tier=CoverageTier.C,
                drop_reasons=[DropReason.NO_DESCRIPTION],
            )
            for i in range(tier_c)
        ]
    )
    return BatchCoverage(
        batch_slug="winter-2026",
        batch_label="Winter 2026",
        source="yc-oss/api",
        source_last_updated=datetime(2026, 2, 8, tzinfo=UTC),
        fetched_at=datetime(2026, 5, 1, 19, 0, tzinfo=UTC),
        upstream_company_count=tier_a + tier_b + tier_c,
        yc_official_count=official,
        tier_a_count=tier_a,
        tier_b_count=tier_b,
        tier_c_count=tier_c,
        records=records,
    )


def _make_companies(coverage: BatchCoverage) -> list[RawCompany]:
    return [
        RawCompany.model_validate(
            {
                "slug": r.slug,
                "name": r.name,
                "batch": "Winter 2026",
                "website": f"https://{r.slug}.example",
                "url": f"https://www.ycombinator.com/companies/{r.slug}",
                "one_liner": f"{r.name} one-liner",
                "long_description": "x" * 100,
                "industry": "B2B",
                "industries": ["B2B"],
                "tags": ["AI", "Developer Tools"],
                "regions": ["United States of America"],
            }
        )
        for r in coverage.records
        if r.tier in (CoverageTier.A, CoverageTier.B)
    ]


def _make_analysis(slug: str, **overrides: object) -> CompanyAnalysis:
    base: dict[str, object] = {
        "slug": slug,
        "industry_primary": Industry.B2B_SAAS,
        "industry_secondary": [],
        "ai_capability": [AICapability.AGENTS],
        "tech_stack": [TechStack.ANTHROPIC],
        "oss_posture": OSSPosture.API_ONLY,
        "oss_evidence_url": None,
        "tagline_rewrite": "AI agents for engineers",
        "confidence": "high",
        "sources": [
            f"https://{slug}.example",
            f"https://www.ycombinator.com/companies/{slug}",
        ],
        "rationale": "test",
    }
    base.update(overrides)
    return CompanyAnalysis.model_validate(base)


def test_coverage_only_dashboard_renders_yc_industry_chart(tmp_path: Path) -> None:
    coverage = _make_coverage()
    companies = _make_companies(coverage)
    out = render(coverage, companies, tmp_path / "dashboard.html")
    html = out.read_text()
    assert "Industry distribution (YC-supplied" in html
    # No LLM-derived charts in coverage-only mode.
    assert "AI capability x industry heatmap" not in html
    # Headline shows official-count denominator.
    assert "of Winter 2026" in html
    # Dropped register named individually.
    assert "Gamma 0" in html


def test_enriched_dashboard_renders_llm_charts(tmp_path: Path) -> None:
    coverage = _make_coverage(tier_a=2, tier_c=1, official=10)
    companies = _make_companies(coverage)
    analyses = [
        _make_analysis("a0"),
        _make_analysis("a1", industry_primary=Industry.HEALTHCARE),
    ]
    out = render(coverage, companies, tmp_path / "dashboard.html", analyses=analyses)
    html = out.read_text()
    assert "AI capability x industry heatmap" in html
    assert "Tech stack signals" in html
    assert "Open-source posture" in html
    assert "Classification confidence" in html
    # The drill-down for the heatmap must be present.
    assert "See per-company capability list" in html
    # Industry chart in enriched mode is the LLM version.
    assert "LLM-classified" in html


def test_low_confidence_rows_excluded_from_charts(tmp_path: Path) -> None:
    coverage = _make_coverage(tier_a=2, tier_c=1)
    companies = _make_companies(coverage)
    analyses = [
        _make_analysis("a0", confidence="high"),
        _make_analysis(
            "a1",
            confidence="low",
            industry_primary=Industry.UNKNOWN,
            ai_capability=[AICapability.UNCLEAR],
        ),
    ]
    out = render(coverage, companies, tmp_path / "dashboard.html", analyses=analyses)
    html = out.read_text()
    # The methodology footer should report the low-confidence count.
    assert "1 low-confidence rows excluded" in html


def test_dropped_register_lists_every_excluded_company(tmp_path: Path) -> None:
    coverage = _make_coverage(tier_a=1, tier_c=3)
    companies = _make_companies(coverage)
    out = render(coverage, companies, tmp_path / "dashboard.html")
    html = out.read_text()
    # All three Tier C companies present by slug.
    for i in range(3):
        assert f"<code>c{i}</code>" in html
    assert "no_description" in html


def test_collect_cited_urls_dedupes_and_includes_oss_evidence() -> None:
    a1 = _make_analysis("acme")
    a2 = _make_analysis("beta", oss_posture=OSSPosture.FULLY_OPEN, oss_evidence_url="https://github.com/beta/repo")
    a3 = _make_analysis("acme")  # duplicate slug — same sources should dedupe
    urls = collect_cited_urls([a1, a2, a3])
    # pydantic HttpUrl normalizes by appending a trailing slash to bare hosts.
    assert any("acme.example" in u for u in urls)
    assert any("ycombinator.com/companies/acme" in u for u in urls)
    assert any("github.com/beta/repo" in u for u in urls)
    # No duplicates.
    assert len(urls) == len(set(urls))


def test_write_broken_links_report_attributes_each_url_to_slugs(tmp_path: Path) -> None:
    a1 = _make_analysis("acme")
    a2 = _make_analysis("beta")
    # Use the normalized form pydantic produces so the slug-attribution lookup matches.
    broken = {
        str(a1.sources[0]): ("dead", "404 Not Found"),
        str(a2.sources[0]): ("dead", "503 Service Unavailable"),
    }
    path = write_broken_links_report(tmp_path, broken, [a1, a2])
    contents = path.read_text()
    assert "acme.example" in contents
    assert "404 Not Found" in contents
    assert "`acme`" in contents
    assert "beta.example" in contents
    assert "`beta`" in contents


def test_dashboard_link_verify_banner_appears_when_allowed_dead_links(tmp_path: Path) -> None:
    coverage = _make_coverage(tier_a=1, tier_c=0, official=2)
    companies = _make_companies(coverage)
    analyses = [_make_analysis("a0")]
    out = render(
        coverage,
        companies,
        tmp_path / "dashboard.html",
        analyses=analyses,
        broken_link_count=2,
        allowed_dead_links=True,
    )
    html = out.read_text()
    assert "2 cited link(s) returned 4xx/5xx" in html
    assert "BROKEN_LINKS.md" in html


def test_dashboard_no_banner_when_no_broken_links(tmp_path: Path) -> None:
    coverage = _make_coverage(tier_a=1, tier_c=0)
    companies = _make_companies(coverage)
    analyses = [_make_analysis("a0")]
    out = render(coverage, companies, tmp_path / "dashboard.html", analyses=analyses)
    html = out.read_text()
    assert "cited link(s) returned 4xx/5xx" not in html


@pytest.mark.parametrize("oss", list(OSSPosture))
def test_all_oss_posture_values_render_without_error(tmp_path: Path, oss: OSSPosture) -> None:
    coverage = _make_coverage(tier_a=1, tier_c=0)
    companies = _make_companies(coverage)
    analyses = [_make_analysis("a0", oss_posture=oss)]
    out = render(coverage, companies, tmp_path / "dashboard.html", analyses=analyses)
    assert out.exists()
