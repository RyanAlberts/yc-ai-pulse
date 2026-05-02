"""Pure-Python chart math, shared by every renderer.

Each function takes the validated CompanyAnalysis cohort plus optional context
and returns plain Python data structures (Counters, dicts, lists). No HTML, no
ECharts JSON, no matplotlib. The rendering layers (``dashboard.py`` for HTML,
``reports/ppt.py`` for the deck, ``reports/docx.py`` for the memo) consume the
output of this module.

This separation is what lets the deck and memo cite the *same* numbers as the
dashboard. The numerical-drift check in ``reports/anti_hallucination.py`` walks
the output of these functions and compares it to the prose in the deck/memo.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ycai.schemas import (
    CompanyAnalysis,
    Industry,
    RawCompany,
)


@dataclass(frozen=True)
class CapabilityHeatmap:
    """Capability x industry matrix for the headline chart."""

    capabilities: list[str]  # row labels (Y axis)
    industries: list[str]  # col labels (X axis)
    matrix: dict[tuple[str, str], int]
    total_keep: int  # rows fed into the heatmap (high+medium confidence)


def keep_for_charts(analyses: Iterable[CompanyAnalysis]) -> list[CompanyAnalysis]:
    """Standard cohort: high + medium confidence only. Low excluded by design.

    Every renderer should pass through this gate. If a chart is computed from
    something else (e.g. the YC tag distribution doesn't go through enrichment),
    it lives outside the LLM-derived analytics and uses different math.
    """
    return [a for a in analyses if a.confidence in ("high", "medium")]


def confidence_breakdown(analyses: Iterable[CompanyAnalysis]) -> Counter[str]:
    return Counter(a.confidence for a in analyses)


def industry_distribution(analyses: Iterable[CompanyAnalysis]) -> Counter[str]:
    """LLM-classified primary industry counts (high+medium only)."""
    keep = keep_for_charts(analyses)
    return Counter(a.industry_primary.value for a in keep)


def capability_heatmap(analyses: Iterable[CompanyAnalysis]) -> CapabilityHeatmap:
    """Capability x industry matrix. Rows are top capabilities by total count;
    columns are top industries by total count."""
    keep = keep_for_charts(analyses)
    matrix: dict[tuple[str, str], int] = defaultdict(int)
    for a in keep:
        for cap in a.ai_capability:
            matrix[(cap.value, a.industry_primary.value)] += 1
    cap_totals: Counter[str] = Counter()
    ind_totals: Counter[str] = Counter()
    for (cap_label, ind_label), v in matrix.items():
        cap_totals[cap_label] += v
        ind_totals[ind_label] += v
    top_caps = [name for name, _ in cap_totals.most_common(8)]
    top_inds = [name for name, _ in ind_totals.most_common(6)]
    cap_set = set(top_caps)
    ind_set = set(top_inds)
    restricted = {(c, i): v for (c, i), v in matrix.items() if c in cap_set and i in ind_set}
    return CapabilityHeatmap(
        capabilities=top_caps,
        industries=top_inds,
        matrix=restricted,
        total_keep=len(keep),
    )


def capability_totals(analyses: Iterable[CompanyAnalysis]) -> Counter[str]:
    """Flat capability counter (any company contributes to every capability it lists)."""
    keep = keep_for_charts(analyses)
    counter: Counter[str] = Counter()
    for a in keep:
        for cap in a.ai_capability:
            counter[cap.value] += 1
    return counter


def tech_stack_distribution(analyses: Iterable[CompanyAnalysis]) -> Counter[str]:
    """Tech stack counter. Empty stack list → counted under 'unknown' for clarity."""
    keep = keep_for_charts(analyses)
    counter: Counter[str] = Counter()
    for a in keep:
        if not a.tech_stack:
            counter["unknown"] += 1
        else:
            for stack in a.tech_stack:
                counter[stack.value] += 1
    return counter


def oss_posture_distribution(analyses: Iterable[CompanyAnalysis]) -> Counter[str]:
    keep = keep_for_charts(analyses)
    return Counter(a.oss_posture.value for a in keep)


def yc_tag_distribution(companies: Iterable[RawCompany]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for c in companies:
        for tag in c.tags:
            counter[tag] += 1
    return counter


def region_distribution(companies: Iterable[RawCompany]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for c in companies:
        for region in c.regions:
            counter[region] += 1
    return counter


def headline_numbers(
    analyses: Iterable[CompanyAnalysis],
    coverage: object | None = None,
) -> dict[str, int]:
    """Numbers any prose layer is allowed to cite. The drift checker compares
    every number in deck/memo prose against this dict.

    When ``coverage`` is provided, also includes the coverage stats (tier
    counts, upstream/official counts) so deck/memo methodology slides can
    cite them.
    """
    keep = keep_for_charts(analyses)
    by_conf = confidence_breakdown(analyses)
    cap_counts = capability_totals(analyses)
    out: dict[str, int] = {
        "total_analyses": sum(by_conf.values()),
        "high_confidence": by_conf["high"],
        "medium_confidence": by_conf["medium"],
        "low_confidence": by_conf["low"],
        "cohort_size": len(keep),
        "agents_count": cap_counts.get("agents", 0),
        "no_ai_count": cap_counts.get("no-ai", 0),
        "rag_count": cap_counts.get("rag", 0),
    }
    if coverage is not None:
        out["upstream_count"] = getattr(coverage, "upstream_company_count", 0)
        official = getattr(coverage, "yc_official_count", None)
        if official:
            out["yc_official_count"] = official
        out["tier_a_count"] = getattr(coverage, "tier_a_count", 0)
        out["tier_b_count"] = getattr(coverage, "tier_b_count", 0)
        out["tier_c_count"] = getattr(coverage, "tier_c_count", 0)
        out["analyzable_count"] = getattr(coverage, "analyzable_count", 0)
        # Percentages the deck/memo headlines reference.
        for pct_attr in ("coverage_pct_of_official", "coverage_pct_of_upstream"):
            value = getattr(coverage, pct_attr, None)
            if value is not None:
                out[pct_attr] = round(value)
    return out


def spotlight_companies(analyses: Iterable[CompanyAnalysis], top_n: int = 6) -> list[CompanyAnalysis]:
    """Pick the ``top_n`` most differentiated high-confidence companies.

    Heuristic:
      - high confidence required
      - score = number of distinct AI capabilities + 2 if industry not 'B2B SaaS'
        (off-the-beaten-path industries are inherently more interesting)
      - tie-break by tagline length (longer = more substantive)
    """
    candidates = [a for a in analyses if a.confidence == "high"]

    def score(a: CompanyAnalysis) -> tuple[int, int]:
        diversity = len(set(c.value for c in a.ai_capability))
        off_beat = 0 if a.industry_primary == Industry.B2B_SAAS else 2
        return (diversity + off_beat, len(a.tagline_rewrite))

    return sorted(candidates, key=score, reverse=True)[:top_n]


def quote_candidates(analyses: Iterable[CompanyAnalysis]) -> list[CompanyAnalysis]:
    """Companies whose tagline_rewrite is suitable for a pull quote.

    The rewrite is short by design (<=140 chars), so we just pick the most
    striking high-confidence ones. The deck consumes this list and renders the
    pull-quote slide using ``tagline_rewrite`` verbatim — no second pass through
    the LLM.
    """
    keep = [a for a in analyses if a.confidence == "high" and len(a.tagline_rewrite) >= 30]
    # Prefer taglines that actually *say something*: avoid the boring ones.
    return sorted(keep, key=lambda a: (-len(a.tagline_rewrite), a.slug))


__all__ = [
    "CapabilityHeatmap",
    "capability_heatmap",
    "capability_totals",
    "confidence_breakdown",
    "headline_numbers",
    "industry_distribution",
    "keep_for_charts",
    "oss_posture_distribution",
    "quote_candidates",
    "region_distribution",
    "spotlight_companies",
    "tech_stack_distribution",
    "yc_tag_distribution",
]
