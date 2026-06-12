"""Tests for ycai.dashboard_company — per-company static pages.

The per-company atlas turns a cohort report into a browseable set of
permalinkable pages. These tests pin the contract: index lists every
high/medium-confidence company, each page renders with the expected
sections, slugs are HTML-escaped, low-confidence rows do NOT get pages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import HttpUrl

from ycai.dashboard_company import (
    render_company_index,
    render_company_page,
    render_company_pages,
)
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


def _make_coverage(*, batch_label: str = "Winter 2026") -> BatchCoverage:
    return BatchCoverage(
        batch_slug="w26",
        batch_label=batch_label,
        source="yc-oss/api",
        source_last_updated=datetime(2026, 2, 8, tzinfo=UTC),
        upstream_company_count=10,
        yc_official_count=12,
        analyzable_count=8,
        tier_a_count=8,
        tier_b_count=0,
        tier_c_count=2,
        records=[CoverageRecord(slug=f"co-{i}", name=f"Company {i}", tier=CoverageTier.A) for i in range(8)],
        fetched_at=datetime(2026, 5, 4, tzinfo=UTC),
    )


def _make_company(slug: str, name: str | None = None) -> RawCompany:
    return RawCompany.model_validate(
        {
            "slug": slug,
            "name": name or slug.title(),
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
    traction: list[TractionSignal] | None = None,
) -> CompanyAnalysis:
    return CompanyAnalysis(
        slug=slug,
        industry_primary=industry,
        industry_secondary=[],
        ai_capability=capabilities or [AICapability.AGENTS],
        tech_stack=[TechStack.ANTHROPIC],
        oss_posture=OSSPosture.CLOSED,
        oss_evidence_url=None,
        tagline_rewrite=f"{slug} delivers value to enterprise.",
        confidence=confidence,
        sources=[HttpUrl(f"https://{slug}.example/")],
        rationale=f"Site explicitly says {slug} builds agents.",
        traction=traction or [],
    )


# ----- index page --------------------------------------------------------------


def test_index_lists_high_and_medium_only(tmp_path: Path) -> None:
    """Low-confidence rows must not appear in the browseable index — they're
    excluded from the cohort charts and the dropped register handles them.
    """
    analyses = [
        _make_analysis("alpha", confidence="high"),
        _make_analysis("beta", confidence="medium"),
        _make_analysis("gamma", confidence="low"),
    ]
    companies = [_make_company(s) for s in ("alpha", "beta", "gamma")]
    out = render_company_index(analyses, companies, coverage=_make_coverage(), output_path=tmp_path / "index.html")
    html = out.read_text()
    assert ">alpha<" in html
    assert ">beta<" in html
    assert ">gamma<" not in html  # excluded
    assert "2 of 2 companies" not in html  # actually says count number
    assert "<strong>2</strong>" in html or "2 companies" in html


def test_index_search_blob_is_lowercased(tmp_path: Path) -> None:
    """data-search must be lowercased so client-side filtering is case-insensitive."""
    analyses = [_make_analysis("Acme-AI")]  # mixed case slug
    companies = [_make_company("Acme-AI", name="Acme AI")]
    out = render_company_index(analyses, companies, coverage=_make_coverage(), output_path=tmp_path / "index.html")
    html = out.read_text()
    assert 'data-search="acme-ai acme ai' in html.lower()


# ----- per-company page --------------------------------------------------------


def test_company_page_renders_required_sections(tmp_path: Path) -> None:
    analysis = _make_analysis(
        "alpha",
        capabilities=[AICapability.AGENTS, AICapability.RAG],
        traction=[
            TractionSignal(
                kind=TractionSignalKind.GITHUB_STARS,
                detail="10.4K stars on alpha/repo",
                source_url=HttpUrl("https://alpha.example/blog"),
            )
        ],
    )
    company = _make_company("alpha", name="Alpha Co")
    out = render_company_page(
        analysis,
        company,
        coverage=_make_coverage(),
        peers=[analysis],
        output_path=tmp_path / "alpha.html",
    )
    html = out.read_text()
    assert "<h1>Alpha Co</h1>" in html
    assert "alpha" in html
    assert "agents" in html
    assert "rag" in html
    assert "10.4K stars" in html  # traction detail rendered
    assert 'href="https://alpha.example/blog"' in html  # source link
    assert "Why this classification" in html  # rationale block
    assert "← Cohort dashboard" in html  # nav back


def test_company_page_escapes_user_content(tmp_path: Path) -> None:
    """A tagline containing HTML must be escaped — not rendered as markup."""
    analysis = _make_analysis("alpha")
    # Force a tagline containing dangerous chars by going through the model.
    analysis = analysis.model_copy(update={"tagline_rewrite": '<script>alert("xss")</script> hostile'})
    company = _make_company("alpha")
    out = render_company_page(
        analysis,
        company,
        coverage=_make_coverage(),
        peers=[analysis],
        output_path=tmp_path / "alpha.html",
    )
    html = out.read_text()
    assert "<script>alert(" not in html  # not rendered as markup
    assert "&lt;script&gt;" in html  # escaped


def test_company_page_siblings_block_links_other_companies_in_industry(tmp_path: Path) -> None:
    a = _make_analysis("alpha", industry=Industry.B2B_SAAS)
    b = _make_analysis("beta", industry=Industry.B2B_SAAS)
    c = _make_analysis("gamma", industry=Industry.HEALTHCARE)
    out = render_company_page(
        a,
        _make_company("alpha"),
        coverage=_make_coverage(),
        peers=[a, b, c],
        output_path=tmp_path / "alpha.html",
    )
    html = out.read_text()
    # Siblings = same industry, not self
    assert 'href="beta.html"' in html
    assert 'href="gamma.html"' not in html
    assert 'href="alpha.html"' not in html  # don't link to self


def test_company_page_omits_evidence_url_when_absent(tmp_path: Path) -> None:
    a = _make_analysis("alpha")
    out = render_company_page(
        a,
        _make_company("alpha"),
        coverage=_make_coverage(),
        peers=[a],
        output_path=tmp_path / "alpha.html",
    )
    html = out.read_text()
    assert "OSS evidence" not in html


# ----- public driver -----------------------------------------------------------


def test_render_company_pages_writes_index_plus_one_page_per_kept_row(tmp_path: Path) -> None:
    analyses = [
        _make_analysis("alpha", confidence="high"),
        _make_analysis("beta", confidence="medium"),
        _make_analysis("gamma", confidence="low"),
    ]
    companies = [_make_company(s) for s in ("alpha", "beta", "gamma")]
    index_path, pages = render_company_pages(_make_coverage(), companies, analyses, output_dir=tmp_path)
    assert index_path == tmp_path / "companies" / "index.html"
    assert index_path.exists()
    assert len(pages) == 2  # gamma excluded
    assert (tmp_path / "companies" / "alpha.html").exists()
    assert (tmp_path / "companies" / "beta.html").exists()
    assert not (tmp_path / "companies" / "gamma.html").exists()
