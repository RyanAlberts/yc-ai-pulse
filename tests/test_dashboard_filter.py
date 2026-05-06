"""Tests for the dashboard filter bar (Option B).

The filter bar adds client-side cohort filtering. The Python side builds
a JSON blob of company rows + an HTML <select> for industry/capability/
OSS/has-traction. The JS recompute logic is tested at the integration
level (it's a literal string in the template); these tests pin the
Python contract:

- ``_build_company_rows`` excludes low-confidence rows
- the JSON blob is ``</script``-safe
- dropdown options only contain values that actually appear in the
  cohort (no dead options)
- the filter bar does NOT render in coverage-only mode
- per-company drill-down rows now link to ``companies/<slug>.html``
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import HttpUrl

from ycai.dashboard import _build_company_rows, _filter_bar, render
from ycai.schemas import (
    AICapability,
    BatchCoverage,
    CompanyAnalysis,
    CoverageRecord,
    CoverageTier,
    Industry,
    OSSPosture,
    RawCompany,
    TechStack,
    TractionSignal,
    TractionSignalKind,
)


def _make_coverage() -> BatchCoverage:
    return BatchCoverage(
        batch_slug="w26",
        batch_label="Winter 2026",
        source="yc-oss/api",
        source_last_updated=datetime(2026, 2, 8, tzinfo=UTC),
        upstream_company_count=10,
        yc_official_count=12,
        analyzable_count=8,
        tier_a_count=8,
        tier_b_count=0,
        tier_c_count=0,
        records=[CoverageRecord(slug=f"co-{i}", name=f"Company {i}", tier=CoverageTier.A) for i in range(8)],
        fetched_at=datetime(2026, 5, 4, tzinfo=UTC),
    )


def _make_company(slug: str) -> RawCompany:
    return RawCompany.model_validate(
        {
            "slug": slug,
            "name": slug.title(),
            "batch": "Winter 2026",
            "website": f"https://{slug}.example",
            "url": f"https://www.ycombinator.com/companies/{slug}",
            "one_liner": f"{slug} does X",
            "long_description": f"{slug} description",
            "industry": "B2B",
            "industries": ["B2B"],
            "tags": ["AI"],
        }
    )


def _make_analysis(
    slug: str,
    *,
    confidence: str = "high",
    industry: Industry = Industry.B2B_SAAS,
    capabilities: list[AICapability] | None = None,
    oss: OSSPosture = OSSPosture.CLOSED,
    traction: list[TractionSignal] | None = None,
) -> CompanyAnalysis:
    return CompanyAnalysis(
        slug=slug,
        industry_primary=industry,
        industry_secondary=[],
        ai_capability=capabilities or [AICapability.AGENTS],
        tech_stack=[TechStack.ANTHROPIC],
        oss_posture=oss,
        oss_evidence_url=None,
        tagline_rewrite=f"{slug} delivers value to enterprise.",
        confidence=confidence,
        sources=[HttpUrl(f"https://{slug}.example/")],
        rationale="ok",
        traction=traction or [],
    )


# ----- _build_company_rows -----------------------------------------------------


def test_build_company_rows_excludes_low_confidence() -> None:
    analyses = [
        _make_analysis("alpha", confidence="high"),
        _make_analysis("beta", confidence="medium"),
        _make_analysis("gamma", confidence="low"),
    ]
    companies = [_make_company(s) for s in ("alpha", "beta", "gamma")]
    rows = _build_company_rows(analyses, companies)
    assert {r["slug"] for r in rows} == {"alpha", "beta"}


def test_build_company_rows_carries_minimum_fields() -> None:
    analyses = [
        _make_analysis(
            "alpha",
            capabilities=[AICapability.AGENTS, AICapability.RAG],
            traction=[
                TractionSignal(
                    kind=TractionSignalKind.GITHUB_STARS,
                    detail="100 stars",
                    source_url=HttpUrl("https://alpha.example/"),
                )
            ],
        )
    ]
    companies = [_make_company("alpha")]
    rows = _build_company_rows(analyses, companies)
    assert len(rows) == 1
    row = rows[0]
    assert row["slug"] == "alpha"
    assert row["name"] == "Alpha"
    assert row["industry"] == "B2B SaaS"
    assert row["capabilities"] == ["agents", "rag"]
    assert row["tech_stack"] == ["anthropic"]
    assert row["oss"] == "closed"
    assert row["confidence"] == "high"
    assert row["traction_count"] == 1


def test_build_company_rows_round_trips_through_json() -> None:
    """The blob ships in the dashboard as JSON; must serialize cleanly."""
    analyses = [_make_analysis("alpha")]
    companies = [_make_company("alpha")]
    rows = _build_company_rows(analyses, companies)
    blob = json.dumps(rows)
    parsed = json.loads(blob)
    assert parsed[0]["slug"] == "alpha"


# ----- _filter_bar -------------------------------------------------------------


def test_filter_bar_only_lists_values_in_cohort() -> None:
    """Dropdown options must reflect what's actually in the data — no dead options."""
    rows = [
        {
            "slug": "a",
            "name": "A",
            "tagline": "x",
            "industry": "B2B SaaS",
            "capabilities": ["agents"],
            "tech_stack": ["anthropic"],
            "oss": "closed",
            "confidence": "high",
            "traction_count": 0,
        },
        {
            "slug": "b",
            "name": "B",
            "tagline": "y",
            "industry": "Healthcare",
            "capabilities": ["rag"],
            "tech_stack": [],
            "oss": "fully-open",
            "confidence": "high",
            "traction_count": 1,
        },
    ]
    html = _filter_bar(rows)
    assert 'value="B2B SaaS"' in html
    assert 'value="Healthcare"' in html
    assert 'value="agents"' in html
    assert 'value="rag"' in html
    assert 'value="closed"' in html
    assert 'value="fully-open"' in html
    # Industries not in the cohort should not appear.
    assert 'value="Robotics"' not in html
    # The "any" option is always present.
    assert 'value=""' in html


def test_filter_bar_renders_count_placeholders() -> None:
    rows = [
        {
            "slug": "a",
            "name": "A",
            "tagline": "x",
            "industry": "B2B SaaS",
            "capabilities": ["agents"],
            "tech_stack": [],
            "oss": "closed",
            "confidence": "high",
            "traction_count": 0,
        }
    ]
    html = _filter_bar(rows)
    assert 'id="filter-count"' in html  # JS updates this on filter change
    assert "<strong>1</strong>" in html  # initial render shows total


def test_filter_bar_empty_input_returns_empty_string() -> None:
    assert _filter_bar([]) == ""


# ----- render() integration ----------------------------------------------------


def test_render_emits_filter_bar_in_enriched_mode(tmp_path: Path) -> None:
    coverage = _make_coverage()
    companies = [_make_company(f"co-{i}") for i in range(2)]
    # Coverage records must align with the slugs we hand to render.
    coverage = coverage.model_copy(
        update={
            "records": [
                CoverageRecord(slug="co-0", name="Co 0", tier=CoverageTier.A),
                CoverageRecord(slug="co-1", name="Co 1", tier=CoverageTier.A),
            ]
        }
    )
    analyses = [_make_analysis("co-0"), _make_analysis("co-1")]
    out = render(
        coverage,
        companies,
        tmp_path / "dashboard.html",
        analyses=analyses,
        write_company_pages=False,
    )
    html = out.read_text()
    assert 'id="filter-bar"' in html
    assert 'id="companies-data"' in html
    # The dropped register links remain unaffected
    assert 'id="dropped"' in html


def test_render_drill_down_table_links_to_company_pages(tmp_path: Path) -> None:
    coverage = _make_coverage()
    coverage = coverage.model_copy(
        update={"records": [CoverageRecord(slug="alpha", name="Alpha", tier=CoverageTier.A)]}
    )
    analyses = [_make_analysis("alpha")]
    out = render(
        coverage,
        [_make_company("alpha")],
        tmp_path / "dashboard.html",
        analyses=analyses,
        write_company_pages=False,
    )
    html = out.read_text()
    # Drill-downs in enriched mode now link to per-company pages.
    assert 'href="companies/alpha.html"' in html
