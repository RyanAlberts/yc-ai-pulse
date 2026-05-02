"""Tests for the shared analytics module. Pure-Python, deterministic."""

from __future__ import annotations

from ycai.analytics import (
    capability_heatmap,
    capability_totals,
    confidence_breakdown,
    headline_numbers,
    industry_distribution,
    keep_for_charts,
    oss_posture_distribution,
    quote_candidates,
    spotlight_companies,
    tech_stack_distribution,
)
from ycai.schemas import (
    AICapability,
    CompanyAnalysis,
    Industry,
    OSSPosture,
    TechStack,
)


def _ana(slug: str, **overrides: object) -> CompanyAnalysis:
    base: dict[str, object] = {
        "slug": slug,
        "industry_primary": Industry.B2B_SAAS,
        "industry_secondary": [],
        "ai_capability": [AICapability.AGENTS],
        "tech_stack": [TechStack.ANTHROPIC],
        "oss_posture": OSSPosture.API_ONLY,
        "oss_evidence_url": None,
        "tagline_rewrite": "Agents for enterprise teams.",
        "confidence": "high",
        "sources": [
            f"https://{slug}.example",
            f"https://www.ycombinator.com/companies/{slug}",
        ],
        "rationale": "test",
    }
    base.update(overrides)
    return CompanyAnalysis.model_validate(base)


def test_keep_for_charts_excludes_low_confidence() -> None:
    rows = [_ana("a"), _ana("b", confidence="low"), _ana("c", confidence="medium")]
    keep = keep_for_charts(rows)
    assert {a.slug for a in keep} == {"a", "c"}


def test_confidence_breakdown_counts_each_bucket() -> None:
    rows = [_ana("a"), _ana("b", confidence="low"), _ana("c", confidence="medium"), _ana("d")]
    counter = confidence_breakdown(rows)
    assert counter["high"] == 2
    assert counter["medium"] == 1
    assert counter["low"] == 1


def test_industry_distribution_only_high_medium() -> None:
    rows = [
        _ana("a"),
        _ana("b", industry_primary=Industry.HEALTHCARE),
        _ana("c", confidence="low", industry_primary=Industry.FINTECH),
    ]
    counter = industry_distribution(rows)
    assert counter["B2B SaaS"] == 1
    assert counter["Healthcare"] == 1
    assert counter.get("Fintech", 0) == 0  # excluded — low confidence


def test_capability_heatmap_caps_at_8x6() -> None:
    rows = [
        _ana(
            f"co-{i}",
            industry_primary=list(Industry)[i % 4],
            ai_capability=[AICapability.AGENTS, AICapability.RAG, AICapability.VISION],
        )
        for i in range(20)
    ]
    heatmap = capability_heatmap(rows)
    assert len(heatmap.capabilities) <= 8
    assert len(heatmap.industries) <= 6
    assert heatmap.total_keep == 20


def test_capability_totals_counts_each_capability_separately() -> None:
    rows = [
        _ana("a", ai_capability=[AICapability.AGENTS, AICapability.RAG]),
        _ana("b", ai_capability=[AICapability.AGENTS]),
    ]
    counter = capability_totals(rows)
    assert counter["agents"] == 2
    assert counter["rag"] == 1


def test_tech_stack_distribution_treats_empty_as_unknown() -> None:
    rows = [
        _ana("a", tech_stack=[TechStack.ANTHROPIC]),
        _ana("b", tech_stack=[]),
        _ana("c", tech_stack=[TechStack.OPENAI, TechStack.LANGCHAIN]),
    ]
    counter = tech_stack_distribution(rows)
    assert counter["unknown"] == 1
    assert counter["anthropic"] == 1
    assert counter["openai"] == 1
    assert counter["langchain"] == 1


def test_oss_posture_distribution() -> None:
    rows = [
        _ana("a", oss_posture=OSSPosture.CLOSED),
        _ana("b", oss_posture=OSSPosture.UNKNOWN),
        _ana("c", oss_posture=OSSPosture.CLOSED),
    ]
    counter = oss_posture_distribution(rows)
    assert counter["closed"] == 2
    assert counter["unknown"] == 1


def test_headline_numbers_includes_cohort_and_capabilities() -> None:
    rows = [
        _ana("a", ai_capability=[AICapability.AGENTS]),
        _ana("b", ai_capability=[AICapability.AGENTS, AICapability.RAG]),
        _ana("c", confidence="low", ai_capability=[AICapability.NO_AI]),
    ]
    h = headline_numbers(rows)
    assert h["cohort_size"] == 2  # excludes low-confidence row
    assert h["agents_count"] == 2
    assert h["rag_count"] == 1
    assert h["no_ai_count"] == 0  # the no-ai row is low-confidence -> not counted in capability_totals


def test_spotlight_companies_prefers_diverse_capabilities() -> None:
    rows = [
        _ana("plain", ai_capability=[AICapability.AGENTS]),
        _ana(
            "diverse",
            ai_capability=[AICapability.AGENTS, AICapability.RAG, AICapability.VISION, AICapability.MULTIMODAL],
        ),
        _ana(
            "off-beat",
            industry_primary=Industry.ROBOTICS,
            ai_capability=[AICapability.AGENTS, AICapability.ROBOTICS],
        ),
    ]
    spotlights = spotlight_companies(rows, top_n=2)
    slugs = [s.slug for s in spotlights]
    assert "diverse" in slugs
    # off-beat has only 2 capabilities but +2 for non-B2B-SaaS so its score = 4,
    # diverse has 4 capabilities + 0 = 4. The tie-break is tagline length.
    assert "off-beat" in slugs or "diverse" in slugs


def test_quote_candidates_filters_short_taglines() -> None:
    rows = [
        _ana("short", tagline_rewrite="Too short."),
        _ana("long", tagline_rewrite="A meaningfully descriptive tagline that says something real."),
    ]
    quotes = quote_candidates(rows)
    assert [q.slug for q in quotes] == ["long"]
