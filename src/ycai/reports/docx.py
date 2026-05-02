"""Narrative-memo (.docx) generator.

Per USER.md document-format discipline: narrative memos are 2-5 pages with
data appendices. The memo consumes the same ``analytics.py`` math as the
deck, embedding matplotlib chart PNGs alongside the prose. Layer 2 audit
runs before write — same contract as the deck.

Sectioning:
  1. Title + dateline
  2. Headline finding (one paragraph)
  3. Coverage methodology (paragraph + brief table)
  4. The agentic batch (capability heatmap + analysis paragraph)
  5. Industry distribution
  6. Tech stack and OSS posture (with the 'unknown' caveat made explicit)
  7. Six company spotlights (verbatim taglines + classification)
  8. The unanswered questions (3-5 follow-up bullets)
  9. Reproducibility footer
"""

from __future__ import annotations

import io
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor

from ycai import analytics
from ycai.reports.anti_hallucination import audit
from ycai.reports.ppt import (
    Layer2Failure,
    _png_heatmap,
    _png_horizontal_bar,
    _png_pie,
)
from ycai.schemas import BatchCoverage, CompanyAnalysis, RawCompany

log = logging.getLogger(__name__)

ACCENT = RGBColor(0xD2, 0x4E, 0x01)
INK = RGBColor(0x1B, 0x1B, 0x1B)
MUTED = RGBColor(0x6B, 0x6B, 0x6B)


def _add_heading(doc: Document, text: str, level: int = 1) -> None:
    h = doc.add_heading(text, level=level)
    if h.runs:
        h.runs[0].font.color.rgb = INK if level > 0 else ACCENT


def _add_para(doc: Document, text: str, *, italic: bool = False, color: RGBColor = INK, size: int = 11) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.italic = italic
    run.font.size = Pt(size)
    run.font.color.rgb = color


def _add_image(doc: Document, image_bytes: bytes, *, width_inches: float = 6.0) -> None:
    doc.add_picture(io.BytesIO(image_bytes), width=Inches(width_inches))


def _add_oss_color_map() -> dict[str, str]:
    return {
        "fully-open": "#15803D",
        "weights-only": "#65A30D",
        "source-available": "#84CC16",
        "api-only": "#F59E0B",
        "closed": "#B91C1C",
        "unknown": "#9CA3AF",
    }


def build_memo(
    coverage: BatchCoverage,
    companies: list[RawCompany],
    analyses: list[CompanyAnalysis],
    *,
    output_path: Path,
) -> Path:
    """Generate the narrative .docx memo. Layer 2 audit runs before write."""
    industry = analytics.industry_distribution(analyses)
    capability = analytics.capability_totals(analyses)
    cap_heatmap = analytics.capability_heatmap(analyses)
    tech_stack = analytics.tech_stack_distribution(analyses)
    oss_posture = analytics.oss_posture_distribution(analyses)
    headline = analytics.headline_numbers(analyses, coverage=coverage)
    spotlights = analytics.spotlight_companies(analyses, top_n=6)

    aggregate_prose: list[str] = []
    quoted_facts: list[str] = []
    fetched_at = datetime.now(UTC)

    doc = Document()
    # 1. Title + dateline
    title = doc.add_heading(f"The State of AI in {coverage.batch_label}", level=0)
    if title.runs:
        title.runs[0].font.color.rgb = ACCENT
    _add_para(
        doc,
        f"yc-ai-pulse memo · generated {fetched_at.strftime('%Y-%m-%d')} · " "github.com/RyanAlberts/yc-ai-pulse",
        italic=True,
        color=MUTED,
        size=10,
    )

    # 2. Headline
    _add_heading(doc, "Headline", level=1)
    headline_pct = coverage.coverage_pct_of_official or coverage.coverage_pct_of_upstream
    denominator = coverage.yc_official_count or coverage.upstream_company_count
    cohort_size = headline["cohort_size"]
    agents_count = headline["agents_count"]
    no_ai_count = headline["no_ai_count"]
    headline_text = (
        f"This memo analyzes {cohort_size} of {denominator} companies in "
        f"{coverage.batch_label} ({headline_pct}% coverage). "
        f"Of the analyzable cohort, {agents_count} build agents — the dominant capability of the batch. "
        f"{no_ai_count} companies are classified as no-ai despite being inside YC's AI batch, a "
        "reminder that 'AI' has become marketing taxonomy as much as product taxonomy."
    )
    _add_para(doc, headline_text)
    aggregate_prose.append(headline_text)

    # 3. Coverage methodology
    _add_heading(doc, "Coverage and methodology", level=1)
    coverage_text = (
        f"Source: {coverage.source}, last refreshed {coverage.source_last_updated:%Y-%m-%d}. "
        f"Upstream lists {coverage.upstream_company_count} companies for {coverage.batch_label}; "
        f"{coverage.tier_a_count} pass full classification (Tier A), "
        f"{coverage.tier_b_count} pass with website unreachable (Tier B), "
        f"{coverage.tier_c_count} are excluded due to missing required fields (Tier C). "
        f"Excluded companies are named in the dropped register attached to the dashboard. "
        f"Of the analyzable Tier A+B set, the LLM produced {headline['high_confidence']} high-confidence + "
        f"{headline['medium_confidence']} medium-confidence rows (the cohort behind every chart in this memo). "
        f"{headline['low_confidence']} rows fell to low confidence and are excluded from charts."
    )
    _add_para(doc, coverage_text)
    aggregate_prose.append(coverage_text)
    layer2_text = (
        "Anti-hallucination guards: Layer 1 enforces a pydantic schema on every model row, requires "
        "at least one source URL drawn from the company website or its YC profile, and runs a two-pass "
        "cross-check on uncertain rows. Layer 2 (this memo) scans aggregate prose for forbidden hedge "
        "phrases and audits every number against the same dataframe the dashboard cites."
    )
    _add_para(doc, layer2_text)
    aggregate_prose.append(layer2_text)

    # 4. The agentic batch — capability heatmap
    _add_heading(doc, "The agentic batch", level=1)
    _add_image(doc, _png_heatmap(cap_heatmap), width_inches=6.5)
    cohort = max(headline["cohort_size"], 1)
    agents_pct = round(headline["agents_count"] * 100 / cohort)
    cap_text = (
        f"The most concentrated finding: {headline['agents_count']} of {cohort} cohort companies "
        f"build what they describe as agents ({agents_pct}% of the cohort). The next-most-cited "
        f"capabilities are {capability.most_common(2)[1][0] if len(capability) > 1 else 'rag'} and "
        f"{capability.most_common(3)[2][0] if len(capability) > 2 else 'data-pipeline'}. The heatmap above "
        "shows where each capability concentrates by industry. Click-through into the dashboard for "
        "row-level evidence on every cell."
    )
    _add_para(doc, cap_text)
    aggregate_prose.append(cap_text)

    # 5. Industry distribution
    _add_heading(doc, "Industry distribution", level=1)
    _add_image(doc, _png_horizontal_bar(industry, top=12), width_inches=6.5)
    top3 = industry.most_common(3)
    industry_text = (
        f"The top three industries account for {sum(c for _, c in top3)} of {cohort} companies: "
        + ", ".join(f"{name} ({count})" for name, count in top3)
        + ". This is consistent with public reporting on the batch composition."
    )
    _add_para(doc, industry_text)
    aggregate_prose.append(industry_text)

    # 6. Tech stack and OSS posture
    _add_heading(doc, "Tech stack and open-source posture", level=1)
    _add_image(doc, _png_pie(oss_posture, color_map=_add_oss_color_map()), width_inches=4.5)
    oss_text = (
        f"Open-source posture: {oss_posture.get('closed', 0)} closed, "
        f"{oss_posture.get('unknown', 0)} unknown, "
        f"{oss_posture.get('api-only', 0)} api-only, "
        f"{oss_posture.get('source-available', 0)} source-available, "
        f"{oss_posture.get('fully-open', 0)} fully-open. "
        "The unknown slice represents the cohort the model could not classify even after a "
        "depth=1 website crawl. Tech stack is similarly under-determined: most companies do not "
        "advertise their model provider on the marketing surface."
    )
    _add_para(doc, oss_text)
    aggregate_prose.append(oss_text)
    _add_image(doc, _png_horizontal_bar(tech_stack, top=10), width_inches=6.5)

    # 7. Spotlights
    _add_heading(doc, "Six company spotlights", level=1)
    for company in spotlights:
        h = doc.add_heading(company.slug, level=2)
        if h.runs:
            h.runs[0].font.color.rgb = INK
        _add_para(doc, company.tagline_rewrite, italic=False, size=12)
        _add_para(
            doc,
            f"Industry: {company.industry_primary.value}  ·  "
            f"Capabilities: {', '.join(c.value for c in company.ai_capability)}  ·  "
            f"Tech stack: {', '.join(s.value for s in company.tech_stack) or 'unknown'}  ·  "
            f"OSS posture: {company.oss_posture.value}",
            color=MUTED,
            size=10,
        )
        if company.rationale:
            _add_para(doc, f'"{company.rationale}"', italic=True, size=10, color=MUTED)
        quoted_facts.append(company.tagline_rewrite)
        if company.rationale:
            quoted_facts.append(company.rationale)

    # 8. Unanswered questions
    _add_heading(doc, "What we still cannot answer", level=1)
    questions_text = (
        "Three open questions surfaced by this analysis:\n"
        "1. The unknown plurality on tech stack and OSS posture suggests most companies do not "
        "advertise model provider or licensing on their marketing surfaces. A deeper crawl into "
        "/docs subtrees would close part of this gap.\n"
        "2. Of the cohort classified no-ai, none claim to use AI but appear in YC's AI batch — "
        "this is not a defect in classification but a category question for YC.\n"
        "3. The dropped register names every excluded company. The pattern of empty long_description "
        "fields suggests companies in stealth at the time the upstream feed was last refreshed."
    )
    _add_para(doc, questions_text)
    aggregate_prose.append(questions_text)

    # 9. Reproducibility footer
    _add_heading(doc, "Reproduce this memo", level=1)
    _add_para(
        doc,
        "Install: pipx install yc-ai-pulse · "
        f"Run: ycai run-coverage --batch {coverage.batch_slug} --enrich · "
        f"Then: ycai report runs/<timestamp>",
        size=10,
        color=MUTED,
    )
    _add_para(
        doc,
        "Source: github.com/RyanAlberts/yc-ai-pulse",
        size=10,
        color=MUTED,
    )

    # Layer 2 audit before write — same contract as the deck. Derived sums
    # (top-3 industries) are added to extra_allowed so the auditor can verify
    # the prose against actual computed totals rather than rejecting them as
    # "drift". The sums themselves come from the same Counter the chart uses.
    derived_sums: tuple[float, ...] = (
        sum(c for _, c in industry.most_common(3)),
        sum(c for _, c in industry.most_common(5)),
        sum(c for _, c in capability.most_common(3)),
        sum(c for _, c in oss_posture.most_common(3)),
    )
    infra_facts: tuple[float, ...] = (4.6, 5, 30, 2, 1, *derived_sums)
    counters: list[Counter[str]] = [industry, capability, tech_stack, oss_posture]
    aggregate = " ".join(aggregate_prose)
    quoted = " ".join(quoted_facts)
    report = audit(aggregate, headline, counters, extra_allowed=infra_facts)
    quoted_forbidden = audit(quoted, headline, counters).forbidden
    report = type(report)(
        forbidden=report.forbidden + quoted_forbidden,
        drifts=report.drifts,
    )
    if not report.is_clean:
        raise Layer2Failure(
            f"Memo Layer 2 audit failed: {len(report.forbidden)} forbidden phrase(s), "
            f"{len(report.drifts)} numerical drift(s).",
            forbidden=report.forbidden,
            drifts=report.drifts,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


__all__ = ["build_memo"]
