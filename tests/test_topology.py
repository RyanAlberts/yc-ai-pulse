"""Tests for ycai.topology — the schema-enforcement and drop-invent guards.

The whole point of this module is to refuse hallucinated output: clusters
that name companies not in the input cohort, essays that cite slugs the
LLM made up, length violations on the paragraph field. These tests pin
those guards so a future relaxation is caught.
"""

from __future__ import annotations

import json

import pytest
from pydantic import HttpUrl

from ycai.researcher import Backend
from ycai.schemas import (
    AICapability,
    CompanyAnalysis,
    Industry,
    OSSPosture,
)
from ycai.topology import (
    _parse_pov,
    _parse_topology,
    cluster_b2b,
    pov_essays,
)


def _make_b2b_analysis(slug: str, capability: AICapability = AICapability.AGENTS) -> CompanyAnalysis:
    return CompanyAnalysis(
        slug=slug,
        industry_primary=Industry.B2B_SAAS,
        industry_secondary=[],
        ai_capability=[capability],
        tech_stack=[],
        oss_posture=OSSPosture.CLOSED,
        oss_evidence_url=None,
        tagline_rewrite=f"Acme tagline for {slug}.",
        confidence="high",
        sources=[HttpUrl(f"https://{slug}.example.com")],
        rationale="ok",
        traction=[],
        yc_subindustry="Sales",
    )


class _ScriptedBackend(Backend):
    """Backend that returns the next pre-set response on each call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, prompt: str, *, model: str = "test") -> str:
        self.calls += 1
        if not self._responses:
            return ""
        return self._responses.pop(0)


# ----- topology parsing ---------------------------------------------------------


def test_parse_topology_drops_invented_members() -> None:
    raw = json.dumps(
        {
            "clusters": [
                {
                    "name": "agentic outbound prospecting",
                    "thesis": "Tools that run outbound on behalf of a sales rep at scale.",
                    "member_slugs": ["alpha", "beta", "made-up-co"],
                },
                {
                    "name": "vertical RAG specialists",
                    "thesis": "Companies indexing high-value verticals like legal, medical.",
                    "member_slugs": ["gamma", "delta", "ghost-corp"],
                },
            ],
            "unclustered": ["bogus-slug"],
            "total_b2b": 5,
        }
    )
    out = _parse_topology(raw, total_b2b=5, valid_slugs={"alpha", "beta", "gamma", "delta"})
    assert out is not None
    assert len(out.clusters) == 2
    assert out.clusters[0].member_slugs == ["alpha", "beta"]  # made-up-co dropped
    assert out.clusters[1].member_slugs == ["gamma", "delta"]  # ghost-corp dropped
    assert out.unclustered == []  # "bogus-slug" not in valid_slugs


def test_parse_topology_drops_clusters_below_min_members() -> None:
    """A cluster of size 1 (after invent-stripping) must be dropped."""
    raw = json.dumps(
        {
            "clusters": [
                {
                    "name": "good cluster",
                    "thesis": "Two real members make this cluster valid.",
                    "member_slugs": ["alpha", "beta"],
                },
                {
                    "name": "tiny cluster",
                    "thesis": "Only one valid member, the rest were invented.",
                    "member_slugs": ["alpha", "ghost-co"],
                },
            ],
            "unclustered": [],
            "total_b2b": 2,
        }
    )
    out = _parse_topology(raw, total_b2b=2, valid_slugs={"alpha", "beta"})
    # Only one cluster has ≥2 valid members, so model_validate fails B2BTopology
    # min_length=2. We accept None as the correct outcome.
    if out is not None:
        # If validation passes (e.g. the schema relaxes), only the good cluster survives.
        assert len(out.clusters) == 1


def test_parse_topology_returns_none_on_garbage() -> None:
    assert _parse_topology("not json", total_b2b=5, valid_slugs=set()) is None
    assert _parse_topology("", total_b2b=5, valid_slugs=set()) is None


def test_parse_topology_returns_none_on_too_few_clusters() -> None:
    """Schema requires min 2 clusters."""
    raw = json.dumps(
        {
            "clusters": [
                {
                    "name": "just one",
                    "thesis": "Schema needs two clusters minimum, this fails.",
                    "member_slugs": ["alpha", "beta"],
                }
            ],
            "unclustered": [],
            "total_b2b": 2,
        }
    )
    out = _parse_topology(raw, total_b2b=2, valid_slugs={"alpha", "beta"})
    assert out is None


# ----- cluster_b2b end-to-end ----------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_b2b_too_few_companies_returns_none() -> None:
    analyses = [_make_b2b_analysis(s) for s in ("a", "b")]  # only 2 — below threshold
    backend = _ScriptedBackend([])
    out = await cluster_b2b(analyses, {}, backend, model="test")
    assert out is None
    assert backend.calls == 0  # short-circuited before any API call


@pytest.mark.asyncio
async def test_cluster_b2b_returns_validated_topology() -> None:
    slugs = ["alpha", "beta", "gamma", "delta", "epsilon"]
    analyses = [_make_b2b_analysis(s) for s in slugs]
    raw = json.dumps(
        {
            "clusters": [
                {
                    "name": "cluster one",
                    "thesis": "First cluster groups three companies that share a pattern.",
                    "member_slugs": ["alpha", "beta", "gamma"],
                },
                {
                    "name": "cluster two",
                    "thesis": "Second cluster groups two companies in a different pattern.",
                    "member_slugs": ["delta", "epsilon"],
                },
            ],
            "unclustered": [],
            "total_b2b": 5,
        }
    )
    backend = _ScriptedBackend([raw])
    out = await cluster_b2b(analyses, {}, backend, model="test")
    assert out is not None
    assert len(out.clusters) == 2
    assert out.total_b2b == 5


# ----- POV essay parsing -------------------------------------------------------


def test_pov_parse_drops_invented_cited_slugs() -> None:
    raw = json.dumps(
        {
            "figure_key": "andreessen",
            "figure_name": "Marc",
            "affiliation": "a16z",
            "paragraph": "x" * 500,
            "cited_slugs": ["alpha", "beta", "ghost-slug"],
        }
    )
    essay = _parse_pov(raw, valid_slugs={"alpha", "beta"}, figure_key="andreessen")
    assert essay is not None
    assert essay.cited_slugs == ["alpha", "beta"]


def test_pov_parse_rejects_too_short_paragraph() -> None:
    raw = json.dumps(
        {
            "figure_key": "andreessen",
            "figure_name": "Marc",
            "affiliation": "a16z",
            "paragraph": "too short",
            "cited_slugs": ["alpha"],
        }
    )
    essay = _parse_pov(raw, valid_slugs={"alpha"}, figure_key="andreessen")
    assert essay is None  # min_length=400 enforced


def test_pov_parse_returns_none_on_garbage() -> None:
    assert _parse_pov("not json", valid_slugs=set(), figure_key="x") is None
    assert _parse_pov("", valid_slugs=set(), figure_key="x") is None


# ----- pov_essays end-to-end ---------------------------------------------------


@pytest.mark.asyncio
async def test_pov_essays_returns_only_validated() -> None:
    slugs = ["alpha", "beta", "gamma", "delta", "epsilon"]
    analyses = [_make_b2b_analysis(s) for s in slugs]
    valid = json.dumps(
        {
            "figure_key": "x",
            "figure_name": "Test",
            "affiliation": "Test",
            "paragraph": "x" * 600,
            "cited_slugs": ["alpha", "beta"],
        }
    )
    invalid = json.dumps({"paragraph": "too short"})  # validation fails
    backend = _ScriptedBackend([valid, invalid, valid])
    figures = {
        "andreessen": {"name": "M", "affiliation": "a16z", "view": "concentrate"},
        "dalio": {"name": "R", "affiliation": "Bridgewater", "view": "diversify"},
        "acemoglu": {"name": "D", "affiliation": "MIT", "view": "redistribute"},
    }
    headline = {"cohort_size": 5, "agents_count": 5, "no_ai_count": 0}
    out = await pov_essays(analyses, {}, headline=headline, figures=figures, backend=backend, model="test")
    assert set(out.keys()) == {"andreessen", "acemoglu"}  # dalio dropped on validation
