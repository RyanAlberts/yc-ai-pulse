"""LLM-driven bottom-up topology and POV essays for the memo.

This module owns the parts of the memo that *think*. PR #15-#17 made the
memo well-structured; the user (correctly) pointed out that the three-POV
section and the "Inside B2B SaaS" section were templated — they restated
the headline numbers in three voices, and they used YC's own taxonomy
("Sales", "Operations") which is just industry-speak.

Two functions here, both run during memo build:

1. ``cluster_b2b()`` — feeds the high-confidence B2B SaaS cohort to a
   Sonnet model, asks for organic clusters that come from what these
   companies *actually do*, with names that wouldn't appear in a YC
   industry-tag dropdown. Each cluster cites its members and a one-line
   thesis.

2. ``pov_essays()`` — generates three substantive 200-300 word essays,
   one per named figure (Andreessen / Dalio / Acemoglu), each grounded in
   specific named companies, traction signals, and external (HN/Reddit)
   discussion when available. The essays are required to disagree
   meaningfully — same data, three different reads of what to do with it.

Both functions return validated pydantic objects. Source-URL grounding
(every citation must trace to an allowed surface) is enforced. The
output flows into ``reports/docx.py``'s "Inside B2B SaaS" and
"Introduction: three views" sections, replacing the templated prose.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ycai.external_signals import CompanyExternalProfile
from ycai.researcher import Backend
from ycai.schemas import CompanyAnalysis, Industry

log = logging.getLogger(__name__)


# =============================================================================
# B2B SaaS topology
# =============================================================================


class TopologyCluster(BaseModel):
    """One organic cluster the model identified.

    The name is required to be a phrase that describes *what these companies
    do*, not an industry-taxonomy label. The thesis is one sentence on why
    these companies belong together. Members are slugs.
    """

    name: str = Field(min_length=4, max_length=80)
    thesis: str = Field(min_length=20, max_length=280)
    member_slugs: list[str] = Field(min_length=2, max_length=12)


class B2BTopology(BaseModel):
    """Output of the clustering pass."""

    clusters: list[TopologyCluster] = Field(min_length=2, max_length=8)
    unclustered: list[str] = Field(default_factory=list)
    total_b2b: int


_TOPOLOGY_PROMPT = """You are clustering Y Combinator B2B SaaS companies into a
bottom-up topology. The goal: surface organic categories that describe what
these companies *actually do*, not the industry-taxonomy buckets a YC
classification dropdown would offer ("Sales", "Operations", "Engineering").

Input: a list of companies, each with a slug, a one-line tagline, the AI
capabilities they advertise, and (when present) their traction signals.

Your job:
1. Read every company. Look for natural groupings around what they DO, not
   the industry they sell into. Examples of organic categories: "agentic
   outbound prospecting", "headless workflow execution for back-office
   teams", "AI native compliance for regulated industries", "vertical RAG
   for legal/medical/finance specialists", "computer-use agents that drive
   existing SaaS UIs", "voice-first ops for offline industries". DO NOT use
   "Sales" or "Operations" — those are not categories, they are dropdown
   labels.
2. Produce 3-6 clusters. Each cluster needs:
   - a name (4-80 chars, the actual phrase that describes what they do)
   - a one-sentence thesis (why these companies belong together — what's
     the underlying pattern)
   - 2-12 member slugs from the input
3. Companies that don't fit a meaningful cluster go to ``unclustered``.
   Don't force cluster membership for marketing reasons — empty clusters
   beat misleading ones.
4. Each company appears in at most one cluster.

Anti-hallucination rules:
- Do not invent companies that aren't in the input.
- Do not invent capabilities or traction the input doesn't mention.
- Names must be specific to what the cohort actually does — not generic.

Companies (slug | tagline | capabilities | traction):
{companies_block}

Total companies in input: {total}

Return ONLY a JSON object matching this schema (no prose, no markdown fences):
{{
  "clusters": [
    {{
      "name": "string, 4-80 chars",
      "thesis": "string, 20-280 chars, one sentence",
      "member_slugs": ["slug1", "slug2", ...]
    }}
  ],
  "unclustered": ["slug1", "slug2", ...],
  "total_b2b": {total}
}}
"""


def _format_b2b_for_prompt(analyses: list[CompanyAnalysis], external: dict[str, CompanyExternalProfile]) -> str:
    """Compact textual representation for the LLM."""
    lines: list[str] = []
    for a in analyses:
        caps = ", ".join(c.value for c in a.ai_capability)
        traction_bits: list[str] = []
        for t in a.traction[:2]:
            traction_bits.append(f"{t.kind.value}: {t.detail[:80]}")
        ext = external.get(a.slug)
        if ext and ext.hn:
            top = max(ext.hn, key=lambda s: s.score)
            traction_bits.append(f'HN: "{top.title[:60]}" ({top.score} pts)')
        if ext and ext.reddit:
            top = max(ext.reddit, key=lambda s: s.score)
            traction_bits.append(f'Reddit: "{top.title[:60]}" ({top.score} ups)')
        if ext and ext.github:
            traction_bits.append(f"GitHub: {ext.github.score} stars")
        traction = "; ".join(traction_bits) if traction_bits else "no signals"
        lines.append(f"- {a.slug} | {a.tagline_rewrite} | caps: {caps} | {traction}")
    return "\n".join(lines)


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_topology(raw: str, *, total_b2b: int, valid_slugs: set[str]) -> B2BTopology | None:
    """Strict-parse + drop-invent guard."""
    if not raw:
        return None
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    payload.setdefault("total_b2b", total_b2b)
    # Drop members the model invented (not in input cohort).
    cleaned_clusters: list[dict[str, Any]] = []
    for c in payload.get("clusters", []):
        members = [s for s in c.get("member_slugs", []) if s in valid_slugs]
        if len(members) >= 2:
            cleaned_clusters.append({**c, "member_slugs": members})
    payload["clusters"] = cleaned_clusters
    payload["unclustered"] = [s for s in payload.get("unclustered", []) if s in valid_slugs]
    try:
        return B2BTopology.model_validate(payload)
    except ValidationError as exc:
        log.debug("topology validation failed: %s", exc)
        return None


async def cluster_b2b(
    analyses: list[CompanyAnalysis],
    external: dict[str, CompanyExternalProfile],
    backend: Backend,
    *,
    model: str,
) -> B2BTopology | None:
    """Build a bottom-up topology of the B2B SaaS sub-cohort. Returns None on failure."""
    keep = [a for a in analyses if a.confidence in ("high", "medium") and a.industry_primary == Industry.B2B_SAAS]
    if len(keep) < 4:
        # Too few rows for clustering to be meaningful.
        return None
    prompt = _TOPOLOGY_PROMPT.format(
        companies_block=_format_b2b_for_prompt(keep, external),
        total=len(keep),
    )
    raw = await backend.complete(prompt, model=model)
    return _parse_topology(raw, total_b2b=len(keep), valid_slugs={a.slug for a in keep})


# =============================================================================
# Three-POV essays
# =============================================================================


class POVEssay(BaseModel):
    figure_key: str  # 'andreessen' | 'dalio' | 'acemoglu'
    figure_name: str
    affiliation: str
    paragraph: str = Field(min_length=400, max_length=2400)
    cited_slugs: list[str] = Field(default_factory=list)


_POV_PROMPT_BASE = """You are writing a serious analysis paragraph from the
perspective of a named public figure, reading the most recent Y Combinator
batch as evidence. The paragraph appears in an investor memo. It is NOT a
summary of headline numbers. It is the figure's argument about what these
specific companies imply for capital allocation, given THEIR published
worldview.

Figure: {name} ({affiliation})
Worldview to argue from:
{worldview}

Cohort summary (numbers come from the dataframe; you may cite them):
- {cohort_size} companies in the high-confidence cohort
- {agents_count} build agents
- {no_ai_count} are classified no-ai despite being in the YC AI batch
- top capabilities: {top_capabilities}
- traction signals: {traction_summary}

A representative slice of the cohort (slug | tagline | capabilities | traction
including HN/Reddit when available):
{companies_block}

Write a single paragraph (200-300 words) that:
1. Names at least 3 specific companies by slug from the input — these are
   the witnesses for the figure's argument.
2. Engages with what those companies *do*, not what category they sit in.
3. Reaches the figure's specific capital-allocation prescription
   (concentrate / diversify / weight redistributive risk).
4. Disagrees with the other two figures meaningfully — the same data should
   imply different actions for them, and your paragraph should hint at
   where you diverge.
5. Avoids hedge phrases ("studies show", "experts say", "many believe").
6. Avoids inventing facts. Every named company must be in the input.
   Numbers must come from the cohort summary above.

Return ONLY a JSON object matching this schema (no prose, no markdown fences):
{{
  "figure_key": "{figure_key}",
  "figure_name": "{name}",
  "affiliation": "{affiliation}",
  "paragraph": "the 200-300 word paragraph as a single string, no newlines",
  "cited_slugs": ["slug1", "slug2", ...]
}}
"""


def _format_cohort_for_pov(
    analyses: list[CompanyAnalysis],
    external: dict[str, CompanyExternalProfile],
    *,
    sample_size: int = 25,
) -> str:
    """Pick a representative slice — every confidence-class with traction is over-sampled."""
    keep = [a for a in analyses if a.confidence in ("high", "medium")]

    def _signal_count(slug: str) -> int:
        ext = external.get(slug)
        return ext.total_count if ext is not None else 0

    # Stable order: signals-first, then by slug for determinism.
    keep.sort(key=lambda a: (-(len(a.traction) + _signal_count(a.slug)), a.slug))
    sample = keep[:sample_size]
    return _format_b2b_for_prompt(sample, external)


def _parse_pov(raw: str, *, valid_slugs: set[str], figure_key: str) -> POVEssay | None:
    if not raw:
        return None
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    # Drop hallucinated slugs.
    payload["cited_slugs"] = [s for s in payload.get("cited_slugs", []) if s in valid_slugs]
    payload.setdefault("figure_key", figure_key)
    try:
        return POVEssay.model_validate(payload)
    except ValidationError as exc:
        log.debug("POV essay validation failed (%s): %s", figure_key, exc)
        return None


async def pov_essays(
    analyses: list[CompanyAnalysis],
    external: dict[str, CompanyExternalProfile],
    *,
    headline: dict[str, int],
    figures: dict[str, dict[str, str]],
    backend: Backend,
    model: str,
) -> dict[str, POVEssay]:
    """Generate one POV essay per figure key. Returns whichever essays passed validation."""
    keep = [a for a in analyses if a.confidence in ("high", "medium")]
    if len(keep) < 4:
        return {}
    valid_slugs = {a.slug for a in keep}
    cohort_block = _format_cohort_for_pov(keep, external)

    # Headline summary the model can quote from.
    cap_counter: dict[str, int] = {}
    for a in keep:
        for cap in a.ai_capability:
            cap_counter[cap.value] = cap_counter.get(cap.value, 0) + 1
    top_caps = sorted(cap_counter.items(), key=lambda x: -x[1])[:5]
    top_capabilities = ", ".join(f"{n} ({c})" for n, c in top_caps)
    traction_total = sum(1 for a in keep if a.traction)
    traction_summary = f"{traction_total} of {len(keep)} companies advertise at least one verifiable signal"

    out: dict[str, POVEssay] = {}
    for key, figure in figures.items():
        prompt = _POV_PROMPT_BASE.format(
            figure_key=key,
            name=figure["name"],
            affiliation=figure["affiliation"],
            worldview=figure["view"],
            cohort_size=headline.get("cohort_size", len(keep)),
            agents_count=headline.get("agents_count", 0),
            no_ai_count=headline.get("no_ai_count", 0),
            top_capabilities=top_capabilities,
            traction_summary=traction_summary,
            companies_block=cohort_block,
        )
        raw = await backend.complete(prompt, model=model)
        essay = _parse_pov(raw, valid_slugs=valid_slugs, figure_key=key)
        if essay is not None:
            out[key] = essay
        else:
            log.warning("POV essay for %s failed validation; will fall back to template", key)
    return out


__all__ = [
    "B2BTopology",
    "POVEssay",
    "TopologyCluster",
    "cluster_b2b",
    "pov_essays",
]
