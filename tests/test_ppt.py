"""Smoke tests for the deck builder. Renders into a tmp dir and asserts the
file is a non-trivial PowerPoint package.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ycai.reports.ppt import Layer2Failure, build_deck
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


def _coverage(tier_a: int = 5, tier_c: int = 1) -> BatchCoverage:
    records = [CoverageRecord(slug=f"a{i}", name=f"Acme {i}", tier=CoverageTier.A) for i in range(tier_a)] + [
        CoverageRecord(
            slug=f"c{i}",
            name=f"Gamma {i}",
            tier=CoverageTier.C,
            drop_reasons=[DropReason.NO_DESCRIPTION],
        )
        for i in range(tier_c)
    ]
    return BatchCoverage(
        batch_slug="winter-2026",
        batch_label="Winter 2026",
        source="yc-oss/api",
        source_last_updated=datetime(2026, 2, 8, tzinfo=UTC),
        fetched_at=datetime(2026, 5, 1, 19, 0, tzinfo=UTC),
        upstream_company_count=tier_a + tier_c,
        yc_official_count=10,
        tier_a_count=tier_a,
        tier_b_count=0,
        tier_c_count=tier_c,
        records=records,
    )


def _companies(coverage: BatchCoverage) -> list[RawCompany]:
    return [
        RawCompany.model_validate(
            {
                "slug": r.slug,
                "name": r.name,
                "batch": "Winter 2026",
                "website": f"https://{r.slug}.example",
                "url": f"https://www.ycombinator.com/companies/{r.slug}",
                "one_liner": "A company",
                "long_description": "x" * 100,
                "industry": "B2B",
                "industries": ["B2B"],
                "tags": ["AI"],
                "regions": ["United States of America"],
            }
        )
        for r in coverage.records
        if r.tier == CoverageTier.A
    ]


def _ana(slug: str, **overrides: object) -> CompanyAnalysis:
    base: dict[str, object] = {
        "slug": slug,
        "industry_primary": Industry.B2B_SAAS,
        "industry_secondary": [],
        "ai_capability": [AICapability.AGENTS],
        "tech_stack": [TechStack.ANTHROPIC],
        "oss_posture": OSSPosture.API_ONLY,
        "oss_evidence_url": None,
        "tagline_rewrite": "AI agents that automate enterprise workflow execution.",
        "confidence": "high",
        "sources": [
            f"https://{slug}.example",
            f"https://www.ycombinator.com/companies/{slug}",
        ],
        "rationale": "Description explicitly mentions agents.",
    }
    base.update(overrides)
    return CompanyAnalysis.model_validate(base)


def test_build_deck_produces_valid_pptx_zip(tmp_path: Path) -> None:
    coverage = _coverage()
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(5)]
    out = build_deck(coverage, companies, analyses, output_path=tmp_path / "deck.pptx")
    assert out.exists()
    assert out.stat().st_size > 10_000  # not an empty file
    # PowerPoint files are valid ZIPs.
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any("ppt/presentation.xml" in n for n in names)
        # At least 7 slides should be created (title, tldr, 4 chart slides, methodology...)
        slide_count = sum(1 for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        assert slide_count >= 7


def test_build_deck_renders_more_than_15_slides_on_realistic_cohort(tmp_path: Path) -> None:
    coverage = _coverage(tier_a=20)
    companies = _companies(coverage)
    analyses = [
        _ana(
            f"a{i}",
            industry_primary=list(Industry)[i % 5],
            ai_capability=[AICapability.AGENTS, AICapability.RAG],
        )
        for i in range(20)
    ]
    out = build_deck(coverage, companies, analyses, output_path=tmp_path / "deck.pptx")
    with zipfile.ZipFile(out) as z:
        slide_count = sum(1 for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        # title + tldr + 4 charts + 2 quotes + 6 spotlights + methodology + reproduce = 16
        assert slide_count >= 12


def test_build_deck_aborts_on_layer2_violation(tmp_path: Path) -> None:
    """If we mutate prose with a forbidden phrase, the deck must refuse to write."""
    import ycai.reports.ppt as ppt_mod

    original = ppt_mod._build_methodology_slide  # type: ignore[attr-defined]

    def poisoned(prs, ctx) -> None:  # type: ignore[no-untyped-def]
        original(prs, ctx)
        ctx.prose_buffer.append("Studies show that this batch is unique.")

    coverage = _coverage()
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(3)]
    monkeypatched = False
    try:
        ppt_mod._build_methodology_slide = poisoned  # type: ignore[attr-defined]
        monkeypatched = True
        with pytest.raises(Layer2Failure) as excinfo:
            build_deck(coverage, companies, analyses, output_path=tmp_path / "deck.pptx")
        assert any(h.phrase == "studies show" for h in excinfo.value.forbidden)
    finally:
        if monkeypatched:
            ppt_mod._build_methodology_slide = original  # type: ignore[attr-defined]


def test_build_deck_handles_empty_quote_list(tmp_path: Path) -> None:
    """Even if no companies qualify as quotes, the deck must still build."""
    coverage = _coverage(tier_a=1)
    companies = _companies(coverage)
    # Single company with too-short tagline → no quote candidates.
    analyses = [_ana("a0", tagline_rewrite="Short.")]
    out = build_deck(coverage, companies, analyses, output_path=tmp_path / "deck.pptx")
    assert out.exists()
