"""Render the dashboard HTML.

Two modes:
  - coverage-only: PR #1 output. Headline is % batch coverage, charts are
    YC-supplied industry/tags/regions (no LLM).
  - enriched: PR #3 output. Adds AI-capability heatmap, tech-stack chart,
    OSS-posture pie, and confidence breakdown. All driven by analyses.json.

PR #12 — visualization layer is now Apache ECharts (client-side, ~300 KB
gzipped from CDN with SRI). Each chart-card holds:
  - an ECharts canvas (interactive: tooltip, zoom-to-fit, click-to-drill)
  - a <details> drill-down that renders the same data as a static table,
    so the page is still useful with JS disabled or when ECharts fails to load
  - role="img" + aria-label for screen readers

In both modes the dropped register is rendered before any chart so quality
issues are unmissable.

Security note: all ECharts options are JSON-serializable and emitted via
``json.dumps``. No JS function strings are sent to the browser — we lean on
ECharts's built-in template-string formatters (``{b}``, ``{c}``, ``{d}``)
to avoid client-side ``eval``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ycai import analytics
from ycai.schemas import (
    BatchCoverage,
    CompanyAnalysis,
    CoverageTier,
    RawCompany,
)

# CDN + SRI hash for the ECharts bundle. Pinned to a specific minor so the
# subresource integrity check stays valid. To bump: re-run
# `openssl dgst -sha384 -binary echarts.min.js | openssl base64 -A`
ECHARTS_VERSION = "5.5.1"
ECHARTS_CDN = f"https://cdn.jsdelivr.net/npm/echarts@{ECHARTS_VERSION}/dist/echarts.min.js"
# pragma: allowlist secret — this is a public subresource-integrity hash, not a credential.
ECHARTS_SRI = "sha384-Mx5lkUEQPM1pOJCwFtUICyX45KNojXbkWdYhkKUKsbv391mavbfoAmONbzkgYPzR"

ACCENT = "#D24E01"

_OSS_COLORS: dict[str, str] = {
    "fully-open": "#15803D",
    "weights-only": "#65A30D",
    "source-available": "#84CC16",
    "api-only": "#F59E0B",
    "closed": "#B91C1C",
    "unknown": "#9CA3AF",
}

_CONFIDENCE_COLORS: dict[str, str] = {
    "high": "#15803D",
    "medium": "#F59E0B",
    "low": "#B91C1C",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ----- chart-card composer -----------------------------------------------------------------


def _chart_card(
    chart_id: str,
    title: str,
    aria_summary: str,
    echarts_option: dict[str, Any] | None,
    drill_summary: str | None = None,
    drill_table_html: str | None = None,
    *,
    height: int = 360,
) -> tuple[str, dict[str, Any] | None]:
    """Return (html_block, option). Option is None when there's no data — the
    card still renders with a static "no data" notice.
    """
    if echarts_option is None:
        empty = '<p style="color: var(--muted);">No data.</p>'
        body = f'<div class="chart-card"><h2>{_escape(title)}</h2>{empty}</div>'
        return body, None

    fallback_html = drill_table_html if drill_summary and drill_table_html else ""
    parts = [
        f'<div class="chart-card"><h2>{_escape(title)}</h2>',
        f'<div id="{chart_id}" class="chart-canvas" '
        f'style="width: 100%; height: {height}px;" '
        f'role="img" aria-label="{_escape(aria_summary)}"></div>',
        f'<noscript><div class="noscript-fallback">{fallback_html}</div></noscript>',
    ]
    if drill_summary and drill_table_html:
        parts.append(f"<details><summary>{_escape(drill_summary)}</summary>{drill_table_html}</details>")
    parts.append("</div>")
    return "".join(parts), echarts_option


def _slug_table(rows: list[tuple[str, str, str]], *, link_to_pages: bool = False) -> str:
    """Drill-down table. When ``link_to_pages`` is True the slug column links
    to the per-company page at ``companies/<slug>.html``.
    """
    cells = []
    for s, n, v in rows:
        slug_html = f"<code>{_escape(s)}</code>"
        if link_to_pages:
            slug_html = f'<a href="companies/{_escape(s)}.html">{slug_html}</a>'
        cells.append(f"<tr><td>{slug_html}</td><td>{_escape(n)}</td><td>{_escape(v)}</td></tr>")
    body = "".join(cells)
    return f"<table><thead><tr><th>Slug</th><th>Name</th><th>Value</th></tr></thead><tbody>{body}</tbody></table>"


# ----- ECharts option builders -------------------------------------------------------------


def _bar_option(counter: Counter[str], top: int = 12) -> dict[str, Any] | None:
    if not counter:
        return None
    items = counter.most_common(top)
    items.reverse()  # ECharts plots bottom-up; reverse so largest is on top
    categories = [name for name, _ in items]
    values = [v for _, v in items]
    return {
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
        },
        "grid": {"left": 200, "right": 60, "top": 16, "bottom": 24, "containLabel": True},
        "xAxis": {"type": "value"},
        "yAxis": {
            "type": "category",
            "data": categories,
            "axisLabel": {"interval": 0, "fontSize": 12},
        },
        "series": [
            {
                "type": "bar",
                "data": values,
                "itemStyle": {"color": ACCENT, "borderRadius": [0, 3, 3, 0]},
                "label": {"show": True, "position": "right"},
            }
        ],
    }


def _pie_option(counter: Counter[str], color_map: dict[str, str]) -> dict[str, Any] | None:
    if not counter:
        return None
    return {
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "horizontal", "bottom": 0, "type": "scroll"},
        "series": [
            {
                "type": "pie",
                "radius": ["40%", "70%"],
                "center": ["50%", "45%"],
                "avoidLabelOverlap": True,
                "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 2},
                "label": {"show": True, "formatter": "{b}\n{d}%", "fontSize": 11},
                "data": [
                    {
                        "name": name,
                        "value": count,
                        "itemStyle": {"color": color_map.get(name, "#9CA3AF")},
                    }
                    for name, count in counter.most_common()
                ],
            }
        ],
    }


def _heatmap_option(
    matrix: dict[tuple[str, str], int],
    rows: list[str],
    cols: list[str],
) -> dict[str, Any] | None:
    if not matrix:
        return None
    data = []
    for r_idx, r_name in enumerate(rows):
        for c_idx, c_name in enumerate(cols):
            value = matrix.get((r_name, c_name), 0)
            data.append([c_idx, r_idx, value])
    max_val = max((v[2] for v in data), default=1) or 1
    return {
        "tooltip": {
            "position": "top",
            # ECharts template strings — {a}=series, {b}=axis, {c}=value triplet.
            # For a heatmap value triplet we can index via {c0}/{c1}/{c2}.
            "formatter": "<b>{c2}</b> companies",
        },
        "grid": {"left": 180, "right": 24, "top": 24, "bottom": 80, "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": cols,
            "splitArea": {"show": True},
            "axisLabel": {"interval": 0, "rotate": 25, "fontSize": 11},
        },
        "yAxis": {
            "type": "category",
            "data": rows,
            "splitArea": {"show": True},
            "axisLabel": {"interval": 0, "fontSize": 11},
        },
        "visualMap": {
            "min": 0,
            "max": max_val,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": 0,
            "inRange": {"color": ["#FFF7ED", "#FED7AA", ACCENT, "#7C2D12"]},
        },
        "series": [
            {
                "name": "companies",
                "type": "heatmap",
                "data": data,
                "label": {"show": True, "fontSize": 10},
                "emphasis": {"itemStyle": {"shadowBlur": 8, "shadowColor": "rgba(0,0,0,0.2)"}},
            }
        ],
    }


# ----- coverage banner --------------------------------------------------------------------


def _coverage_alert(coverage: BatchCoverage) -> str:
    notes: list[str] = []
    if coverage.yc_official_count and coverage.yc_official_count > coverage.upstream_company_count:
        gap = coverage.yc_official_count - coverage.upstream_company_count
        pct = round(100.0 * coverage.upstream_company_count / coverage.yc_official_count, 1)
        notes.append(
            f"<strong>Upstream gap:</strong> our data source ({coverage.source}) lists "
            f"{coverage.upstream_company_count} companies for {coverage.batch_label}, but the "
            f"actual batch has {coverage.yc_official_count} ({gap} missing, {pct}% present). "
            "Likely upstream staleness."
        )
    if coverage.tier_c_count > 0:
        notes.append(
            f"<strong>Per-company drops:</strong> {coverage.tier_c_count} companies in the "
            "upstream feed were excluded due to missing required fields. They are listed by name in the "
            '<a href="#dropped">dropped register</a> below.'
        )
    if not notes:
        return ""
    return f'<div class="alert">{"<br/>".join(notes)}</div>'


def _dropped_table(coverage: BatchCoverage) -> str:
    drops = [r for r in coverage.records if r.tier == CoverageTier.C]
    if not drops:
        return '<p style="color: var(--muted);">None — every upstream company met the data-quality bar.</p>'
    body = "".join(
        f"<tr><td><code>{_escape(r.slug)}</code></td>"
        f"<td>{_escape(r.name)}</td>"
        f'<td class="reason">{_escape(", ".join(reason.value for reason in r.drop_reasons))}</td></tr>'
        for r in sorted(drops, key=lambda r: r.slug)
    )
    return "<table><thead><tr><th>Slug</th><th>Name</th><th>Reasons</th></tr></thead>" f"<tbody>{body}</tbody></table>"


# ----- chart card builders ----------------------------------------------------------------


def _confidence_card(analyses: list[CompanyAnalysis]) -> tuple[str, str, dict[str, Any] | None]:
    by_conf = analytics.confidence_breakdown(analyses)
    option = _pie_option(by_conf, _CONFIDENCE_COLORS)
    summary = f"high {by_conf['high']} · medium {by_conf['medium']} · low {by_conf['low']}"
    aria = f"Pie chart of LLM classification confidence: {summary}."
    body, _ = _chart_card(
        "chart-confidence",
        f"Classification confidence — {summary}",
        aria,
        option,
        height=320,
    )
    return body, "chart-confidence", option


def _industry_card(analyses: list[CompanyAnalysis]) -> tuple[str, str, dict[str, Any] | None]:
    high = analytics.keep_for_charts(analyses)
    by_ind = analytics.industry_distribution(analyses)
    option = _bar_option(by_ind, top=12)
    rows = [(a.slug, a.tagline_rewrite[:60], a.industry_primary.value) for a in high]
    drill = _slug_table(rows, link_to_pages=True)
    body, _ = _chart_card(
        "chart-industry",
        "Industry distribution (LLM-classified, high+medium confidence only)",
        f"Bar chart of {len(high)} companies grouped by industry. "
        f"Top: {', '.join(f'{n} {c}' for c, n in by_ind.most_common(3))}.",
        option,
        f"See {len(high)} source rows",
        drill,
        height=420,
    )
    return body, "chart-industry", option


def _capability_card(analyses: list[CompanyAnalysis]) -> tuple[str, str, dict[str, Any] | None]:
    keep = analytics.keep_for_charts(analyses)
    heatmap = analytics.capability_heatmap(analyses)
    option = _heatmap_option(dict(heatmap.matrix), heatmap.capabilities, heatmap.industries)
    rows = []
    for a in keep:
        caps = ", ".join(cap.value for cap in a.ai_capability)
        rows.append((a.slug, a.tagline_rewrite[:60], f"{a.industry_primary.value} | {caps}"))
    drill = _slug_table(rows, link_to_pages=True)
    aria = (
        f"Heatmap of {len(heatmap.capabilities)} AI capabilities across "
        f"{len(heatmap.industries)} top industries. "
        f"{heatmap.total_keep} companies in cohort."
    )
    body, _ = _chart_card(
        "chart-capability",
        "AI capability x industry heatmap",
        aria,
        option,
        "See per-company capability list",
        drill,
        height=460,
    )
    return body, "chart-capability", option


def _stack_card(analyses: list[CompanyAnalysis]) -> tuple[str, str, dict[str, Any] | None]:
    keep = analytics.keep_for_charts(analyses)
    by_stack = analytics.tech_stack_distribution(analyses)
    option = _bar_option(by_stack, top=10)
    rows: list[tuple[str, str, str]] = []
    for a in keep:
        rows.append((a.slug, a.tagline_rewrite[:60], ", ".join(s.value for s in a.tech_stack) or "unknown"))
    drill = _slug_table(rows, link_to_pages=True)
    aria = f"Bar chart of tech stack mentions across {len(keep)} high-confidence companies."
    body, _ = _chart_card(
        "chart-stack",
        "Tech stack signals (where determinable from public surfaces)",
        aria,
        option,
        "See per-company stack",
        drill,
        height=400,
    )
    return body, "chart-stack", option


def _oss_card(analyses: list[CompanyAnalysis]) -> tuple[str, str, dict[str, Any] | None]:
    keep = analytics.keep_for_charts(analyses)
    by_oss = analytics.oss_posture_distribution(analyses)
    option = _pie_option(by_oss, _OSS_COLORS)
    rows: list[tuple[str, str, str]] = []
    for a in keep:
        evidence = str(a.oss_evidence_url) if a.oss_evidence_url else "—"
        rows.append((a.slug, a.tagline_rewrite[:60], f"{a.oss_posture.value}  ({evidence})"))
    drill = _slug_table(rows, link_to_pages=True)
    aria = f"Pie chart of open-source posture across {len(keep)} companies."
    body, _ = _chart_card(
        "chart-oss",
        "Open-source posture",
        aria,
        option,
        "See per-company posture + evidence URL",
        drill,
        height=380,
    )
    return body, "chart-oss", option


def _coverage_only_industry_card(
    coverage: BatchCoverage, companies: list[RawCompany]
) -> tuple[str, str, dict[str, Any] | None]:
    analyzable = {r.slug for r in coverage.records if r.tier in (CoverageTier.A, CoverageTier.B)}
    keepers = [c for c in companies if c.slug in analyzable]
    industries: Counter[str] = Counter()
    for c in keepers:
        if c.industry:
            industries[c.industry] += 1
    option = _bar_option(industries, top=12)
    rows = [(c.slug, c.name, c.industry) for c in keepers]
    drill = _slug_table(rows)
    aria = f"Bar chart of {len(keepers)} companies grouped by YC-supplied industry."
    body, _ = _chart_card(
        "chart-industry-yc",
        "Industry distribution (YC-supplied, no LLM)",
        aria,
        option,
        f"See source rows ({len(keepers)} companies)",
        drill,
        height=420,
    )
    return body, "chart-industry-yc", option


def _analysis_section(
    analyses: list[CompanyAnalysis] | None,
    companies: list[RawCompany],
    coverage: BatchCoverage,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Returns (section_html, {chart_id: option})."""
    options: dict[str, dict[str, Any]] = {}
    parts: list[str] = []

    if not analyses:
        body, chart_id, opt = _coverage_only_industry_card(coverage, companies)
        parts.append(body)
        if opt:
            options[chart_id] = opt
        return "\n".join(parts), options

    for builder in (_confidence_card, _industry_card, _capability_card, _stack_card, _oss_card):
        body, chart_id, opt = builder(analyses)
        parts.append(body)
        if opt:
            options[chart_id] = opt
    return "\n".join(parts), options


def _yc_extra_charts(coverage: BatchCoverage, companies: list[RawCompany]) -> tuple[str, dict[str, dict[str, Any]]]:
    """The two YC-supplied charts that always render: top tags + regions."""
    analyzable = {r.slug for r in coverage.records if r.tier in (CoverageTier.A, CoverageTier.B)}
    keepers = [c for c in companies if c.slug in analyzable]
    tags: Counter[str] = Counter()
    regions: Counter[str] = Counter()
    for c in keepers:
        for t in c.tags:
            tags[t] += 1
        for r in c.regions:
            regions[r] += 1

    options: dict[str, dict[str, Any]] = {}
    parts: list[str] = []

    tag_body, tag_opt = _chart_card(
        "chart-tags",
        "YC tag distribution (top 20)",
        f"Bar chart of YC tag mentions across {len(keepers)} companies.",
        _bar_option(tags, top=20),
        height=520,
    )
    parts.append(tag_body)
    if tag_opt:
        options["chart-tags"] = tag_opt

    region_body, region_opt = _chart_card(
        "chart-regions",
        "Region distribution (top 12)",
        f"Bar chart of region mentions across {len(keepers)} companies.",
        _bar_option(regions, top=12),
        height=400,
    )
    parts.append(region_body)
    if region_opt:
        options["chart-regions"] = region_opt

    return "\n".join(parts), options


def _build_company_rows(analyses: list[CompanyAnalysis], companies: list[RawCompany]) -> list[dict[str, Any]]:
    """Compact per-company records for the client-side filter bar.

    Only Tier A+B + high/medium-confidence rows are included (these are
    exactly the rows that feed the charts). Each record carries the
    minimum fields the filter + recompute logic needs:
    slug, name, tagline, industry, capabilities, tech_stack, oss_posture,
    confidence, traction_count.
    """
    name_by_slug = {c.slug: c.name for c in companies}
    rows: list[dict[str, Any]] = []
    for a in analyses:
        if a.confidence == "low":
            continue
        rows.append(
            {
                "slug": a.slug,
                "name": name_by_slug.get(a.slug, a.slug),
                "tagline": a.tagline_rewrite,
                "industry": a.industry_primary.value,
                "capabilities": [c.value for c in a.ai_capability],
                "tech_stack": [s.value for s in a.tech_stack],
                "oss": a.oss_posture.value,
                "confidence": a.confidence,
                "traction_count": len(a.traction),
            }
        )
    return rows


def _filter_bar(rows: list[dict[str, Any]]) -> str:
    """Build the filter UI. ``rows`` is the same list used by the JS recompute,
    so we can populate dropdowns with only values that actually appear in the
    cohort (no dead options).
    """
    if not rows:
        return ""

    industries = sorted({r["industry"] for r in rows})
    capabilities = sorted({c for r in rows for c in r["capabilities"]})
    oss_postures = sorted({r["oss"] for r in rows})

    def _options(values: list[str]) -> str:
        opts = ['<option value="">— any —</option>']
        opts.extend(f'<option value="{_escape(v)}">{_escape(v)}</option>' for v in values)
        return "".join(opts)

    total = len(rows)
    return f"""
  <div class="filter-bar" id="filter-bar" role="region" aria-label="Cohort filter">
    <div class="filter-row">
      <label class="filter-field" style="flex: 1.5; min-width: 200px;">
        <span>Search</span>
        <input type="search" id="f-q" placeholder="slug, name, or tagline…" />
      </label>
      <label class="filter-field">
        <span>Industry</span>
        <select id="f-industry">{_options(industries)}</select>
      </label>
      <label class="filter-field">
        <span>Capability</span>
        <select id="f-capability">{_options(capabilities)}</select>
      </label>
      <label class="filter-field">
        <span>OSS posture</span>
        <select id="f-oss">{_options(oss_postures)}</select>
      </label>
      <label class="filter-checkbox">
        <input type="checkbox" id="f-traction" />
        <span>Has traction</span>
      </label>
      <button type="button" id="f-reset" class="filter-reset">Reset</button>
    </div>
    <div class="filter-status">
      Showing <strong id="filter-count">{total}</strong> of <strong>{total}</strong> companies.
      <a href="companies/index.html">Browse all →</a>
    </div>
  </div>
"""


def _link_verify_banner(broken_count: int, allowed_dead: bool) -> str:
    if broken_count == 0:
        return ""
    if allowed_dead:
        return (
            f'<div class="alert alert-bad">'
            f"<strong>⚠ {broken_count} cited link(s) returned 4xx/5xx</strong> at publish time. "
            "Charts still rendered because of <code>--allow-dead-links</code>. "
            "Broken URLs listed in <code>BROKEN_LINKS.md</code> next to this dashboard."
            "</div>"
        )
    return f'<div class="alert alert-bad"><strong>{broken_count} dead links detected.</strong></div>'


# ----- top-level template ------------------------------------------------------------------


DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{batch_label} — yc-ai-pulse report</title>
<style>
  :root {{
    --bg: #F5F1E8;
    --fg: #1B1B1B;
    --muted: #6B6B6B;
    --accent: #D24E01;
    --warn: #B45309;
    --ok: #15803D;
    --bad: #B91C1C;
    --line: #DDD8CB;
  }}
  body {{ background: var(--bg); color: var(--fg); font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Source Serif Pro", Georgia, serif; margin: 0; padding: 0; }}
  .wrap {{ max-width: 1100px; margin: 40px auto; padding: 0 24px; }}
  h1 {{ font-size: 36px; margin: 0 0 8px; }}
  .subtitle {{ color: var(--muted); margin: 0 0 32px; }}
  .headline {{ background: white; border: 1px solid var(--line); padding: 24px 28px; margin: 0 0 24px; border-radius: 4px; }}
  .headline-num {{ font-size: 64px; font-weight: 600; letter-spacing: -1px; line-height: 1; color: var(--accent); }}
  .headline-num small {{ font-size: 24px; color: var(--muted); font-weight: 400; }}
  .headline-label {{ color: var(--muted); margin-top: 8px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin: 24px 0; }}
  .stat {{ background: white; border: 1px solid var(--line); padding: 18px 20px; border-radius: 4px; }}
  .stat .num {{ font-size: 32px; font-weight: 600; }}
  .stat .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .ok {{ color: var(--ok); }} .warn {{ color: var(--warn); }} .bad {{ color: var(--bad); }}
  details {{ border: 1px solid var(--line); background: white; padding: 14px 20px; margin: 16px 0; border-radius: 4px; }}
  summary {{ cursor: pointer; font-weight: 500; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
  th {{ background: #FAFAFA; font-weight: 600; }}
  td.reason {{ color: var(--muted); font-family: ui-monospace, monospace; font-size: 12px; }}
  .chart-card {{ background: white; border: 1px solid var(--line); padding: 20px; margin: 16px 0; border-radius: 4px; }}
  .chart-card h2 {{ margin: 0 0 16px; font-size: 18px; }}
  .chart-canvas {{ background: transparent; }}
  .noscript-fallback {{ padding: 12px; background: #FFF7ED; border: 1px dashed var(--line); }}
  footer {{ color: var(--muted); font-size: 13px; margin: 48px 0 24px; padding-top: 24px; border-top: 1px solid var(--line); }}
  .alert {{ background: #FFF7ED; border-left: 4px solid var(--warn); padding: 14px 18px; margin: 16px 0; border-radius: 4px; }}
  .alert-bad {{ background: #FEE2E2; border-left-color: var(--bad); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 500; background: #EEE; color: #555; }}
  .badge.tier-A {{ background: #DCFCE7; color: #15803D; }}
  .badge.tier-B {{ background: #FEF3C7; color: #B45309; }}
  .badge.tier-C {{ background: #FEE2E2; color: #B91C1C; }}
  .filter-bar {{ background: white; border: 1px solid var(--line); padding: 16px 20px; border-radius: 4px; margin: 24px 0 16px; }}
  .filter-row {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }}
  .filter-field {{ display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 140px; }}
  .filter-field span {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; color: var(--muted); }}
  .filter-field input[type=search], .filter-field select {{
    padding: 8px 10px; border: 1px solid var(--line); border-radius: 3px;
    font-size: 14px; background: white; color: var(--fg); }}
  .filter-checkbox {{ display: flex; align-items: center; gap: 6px; font-size: 14px; padding-bottom: 8px; }}
  .filter-checkbox input {{ margin: 0; }}
  .filter-reset {{ padding: 8px 14px; background: white; border: 1px solid var(--line); border-radius: 3px;
    font-size: 13px; cursor: pointer; color: var(--muted); }}
  .filter-reset:hover {{ background: #FAF6E8; color: var(--fg); }}
  .filter-status {{ font-size: 13px; color: var(--muted); margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--line); }}
  .filter-status a {{ color: var(--accent); margin-left: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>The State of AI in {batch_label}</h1>
  <p class="subtitle">yc-ai-pulse report · generated {fetched_at} · source {source}</p>

  <div class="headline">
    <div class="headline-num">{headline_pct}%<small> of {batch_label}</small></div>
    <div class="headline-label">analyzed in this report</div>
    <p style="margin: 12px 0 0; color: var(--muted); max-width: 70ch;">
      {analyzable_count} of {denominator} companies passed the data-quality gate.
      Tier A and B feed every chart in this report. Tier C companies are
      <a href="#dropped">acknowledged below</a> with the specific reason they were excluded — no quiet drops.
    </p>
  </div>

  {coverage_alert}
  {link_verify_banner}

  <div class="grid">
    <div class="stat"><div class="num ok">{tier_a}</div><div class="label">Tier A · full classification</div></div>
    <div class="stat"><div class="num warn">{tier_b}</div><div class="label">Tier B · partial (website unreachable)</div></div>
    <div class="stat"><div class="num bad">{tier_c}</div><div class="label">Tier C · excluded</div></div>
  </div>

  {filter_bar}
  {analysis_section}
  {yc_extra_section}

  <h2 id="dropped" style="margin-top: 48px;">Dropped register · {tier_c} excluded companies</h2>
  <p style="color: var(--muted); max-width: 70ch;">
    Every company excluded from the analysis above is listed here with the
    specific reason. We do not silently drop data.
  </p>
  {dropped_table}

  <footer>
    <p><strong>Methodology.</strong> {methodology_text}</p>
    <p><strong>Reproduce this run.</strong>
    Source code: <a href="https://github.com/RyanAlberts/yc-ai-pulse">github.com/RyanAlberts/yc-ai-pulse</a>.
    Run <code>ycai run-coverage --batch {batch_slug} --enrich</code> to regenerate.
    </p>
    <p style="margin-top: 12px; font-size: 11px;">Charts rendered with <a href="https://echarts.apache.org/">Apache ECharts</a> {echarts_version}.</p>
  </footer>

  <script type="application/json" id="raw-data">{raw_data_json}</script>
  <script type="application/json" id="companies-data">{companies_json}</script>
</div>

<script src="{echarts_cdn}"
        integrity="{echarts_sri}"
        crossorigin="anonymous"></script>
<script type="application/json" id="chart-options">{chart_options_json}</script>
<script>
(function() {{
  if (typeof echarts === 'undefined') {{
    document.querySelectorAll('.chart-canvas').forEach(function(el) {{
      var p = document.createElement('p');
      p.style.color = '#B91C1C';
      p.textContent = 'ECharts failed to load. See the drill-down tables below for the underlying data.';
      el.parentNode.replaceChild(p, el);
    }});
    return;
  }}
  var ACCENT = '#D24E01';
  var OSS_COLORS = {{
    'fully-open': '#15803D', 'weights-only': '#65A30D', 'source-available': '#84CC16',
    'api-only': '#F59E0B', 'closed': '#B91C1C', 'unknown': '#9CA3AF'
  }};

  // ----- baseline render from server-built options
  var raw = document.getElementById('chart-options').textContent;
  var staticOptions = JSON.parse(raw);
  var instances = {{}};
  Object.keys(staticOptions).forEach(function(id) {{
    var el = document.getElementById(id);
    if (!el) return;
    var inst = echarts.init(el, null, {{ renderer: 'canvas' }});
    inst.setOption(staticOptions[id]);
    instances[id] = inst;
  }});
  window.addEventListener('resize', function() {{
    Object.keys(instances).forEach(function(k) {{ instances[k].resize(); }});
  }});

  // ----- filter bar (no-op if companies-data is missing or empty)
  var dataNode = document.getElementById('companies-data');
  if (!dataNode || !dataNode.textContent.trim()) return;
  var rows;
  try {{ rows = JSON.parse(dataNode.textContent); }} catch (e) {{ return; }}
  if (!Array.isArray(rows) || rows.length === 0) return;

  function counter(values) {{
    var c = {{}};
    values.forEach(function(v) {{ c[v] = (c[v] || 0) + 1; }});
    return c;
  }}
  function topEntries(c, top) {{
    return Object.keys(c).map(function(k) {{ return [k, c[k]]; }})
      .sort(function(a, b) {{ return b[1] - a[1]; }})
      .slice(0, top || 1000);
  }}
  function barOption(entries) {{
    if (!entries.length) return null;
    var rev = entries.slice().reverse();
    return {{
      tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
      grid: {{ left: 200, right: 60, top: 16, bottom: 24, containLabel: true }},
      xAxis: {{ type: 'value' }},
      yAxis: {{ type: 'category', data: rev.map(function(e) {{ return e[0]; }}),
        axisLabel: {{ interval: 0, fontSize: 12 }} }},
      series: [{{ type: 'bar', data: rev.map(function(e) {{ return e[1]; }}),
        itemStyle: {{ color: ACCENT, borderRadius: [0, 3, 3, 0] }},
        label: {{ show: true, position: 'right' }} }}]
    }};
  }}
  function pieOption(entries, colorMap) {{
    if (!entries.length) return null;
    return {{
      tooltip: {{ trigger: 'item', formatter: '{{b}}: {{c}} ({{d}}%)' }},
      legend: {{ orient: 'horizontal', bottom: 0, type: 'scroll' }},
      series: [{{
        type: 'pie', radius: ['40%', '70%'], center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: {{ borderRadius: 4, borderColor: '#fff', borderWidth: 2 }},
        label: {{ show: true, formatter: '{{b}}\\n{{d}}%', fontSize: 11 }},
        data: entries.map(function(e) {{
          return {{ name: e[0], value: e[1],
            itemStyle: {{ color: (colorMap || {{}})[e[0]] || '#9CA3AF' }} }};
        }})
      }}]
    }};
  }}
  function heatmapOption(filtered) {{
    if (!filtered.length) return null;
    var indCounts = counter(filtered.map(function(r) {{ return r.industry; }}));
    var capCounts = {{}};
    filtered.forEach(function(r) {{ r.capabilities.forEach(function(c) {{
      capCounts[c] = (capCounts[c] || 0) + 1; }}); }});
    var industries = topEntries(indCounts, 8).map(function(e) {{ return e[0]; }});
    var capabilities = topEntries(capCounts, 10).map(function(e) {{ return e[0]; }}).reverse();
    var data = [];
    var maxV = 1;
    capabilities.forEach(function(cap, ri) {{
      industries.forEach(function(ind, ci) {{
        var v = filtered.filter(function(r) {{
          return r.industry === ind && r.capabilities.indexOf(cap) !== -1;
        }}).length;
        data.push([ci, ri, v]);
        if (v > maxV) maxV = v;
      }});
    }});
    return {{
      tooltip: {{ position: 'top', formatter: '<b>{{c2}}</b> companies' }},
      grid: {{ left: 180, right: 24, top: 24, bottom: 80, containLabel: true }},
      xAxis: {{ type: 'category', data: industries, splitArea: {{ show: true }},
        axisLabel: {{ interval: 0, rotate: 25, fontSize: 11 }} }},
      yAxis: {{ type: 'category', data: capabilities, splitArea: {{ show: true }},
        axisLabel: {{ interval: 0, fontSize: 11 }} }},
      visualMap: {{ min: 0, max: maxV, calculable: true, orient: 'horizontal',
        left: 'center', bottom: 0,
        inRange: {{ color: ['#FFF7ED', '#FED7AA', ACCENT, '#7C2D12'] }} }},
      series: [{{ name: 'companies', type: 'heatmap', data: data,
        label: {{ show: true, fontSize: 10 }},
        emphasis: {{ itemStyle: {{ shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.2)' }} }} }}]
    }};
  }}

  function applyFilter(filter) {{
    return rows.filter(function(r) {{
      if (filter.industry && r.industry !== filter.industry) return false;
      if (filter.capability && r.capabilities.indexOf(filter.capability) === -1) return false;
      if (filter.oss && r.oss !== filter.oss) return false;
      if (filter.hasTraction && r.traction_count === 0) return false;
      if (filter.q) {{
        var hay = (r.slug + ' ' + r.name + ' ' + r.tagline).toLowerCase();
        if (hay.indexOf(filter.q) === -1) return false;
      }}
      return true;
    }});
  }}

  function recompute(filter) {{
    var filtered = applyFilter(filter);
    var countEl = document.getElementById('filter-count');
    if (countEl) countEl.textContent = String(filtered.length);

    if (instances['chart-confidence']) {{
      var cc = counter(filtered.map(function(r) {{ return r.confidence; }}));
      var opt = pieOption(topEntries(cc),
        {{ high: '#15803D', medium: '#F59E0B', low: '#B91C1C' }});
      if (opt) instances['chart-confidence'].setOption(opt, true);
    }}
    if (instances['chart-industry']) {{
      var ic = counter(filtered.map(function(r) {{ return r.industry; }}));
      var iopt = barOption(topEntries(ic, 12));
      if (iopt) instances['chart-industry'].setOption(iopt, true);
    }}
    if (instances['chart-capability']) {{
      var hopt = heatmapOption(filtered);
      if (hopt) instances['chart-capability'].setOption(hopt, true);
    }}
    if (instances['chart-stack']) {{
      var sc = {{}};
      filtered.forEach(function(r) {{ r.tech_stack.forEach(function(s) {{
        sc[s] = (sc[s] || 0) + 1; }}); }});
      var sopt = barOption(topEntries(sc, 10));
      if (sopt) instances['chart-stack'].setOption(sopt, true);
    }}
    if (instances['chart-oss']) {{
      var oc = counter(filtered.map(function(r) {{ return r.oss; }}));
      var oopt = pieOption(topEntries(oc), OSS_COLORS);
      if (oopt) instances['chart-oss'].setOption(oopt, true);
    }}
  }}

  function readFilter() {{
    return {{
      q: (document.getElementById('f-q').value || '').trim().toLowerCase(),
      industry: document.getElementById('f-industry').value,
      capability: document.getElementById('f-capability').value,
      oss: document.getElementById('f-oss').value,
      hasTraction: document.getElementById('f-traction').checked
    }};
  }}
  function onChange() {{ recompute(readFilter()); }}
  ['f-q', 'f-industry', 'f-capability', 'f-oss', 'f-traction'].forEach(function(id) {{
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', onChange);
    el.addEventListener('change', onChange);
  }});
  var resetBtn = document.getElementById('f-reset');
  if (resetBtn) {{
    resetBtn.addEventListener('click', function() {{
      ['f-q', 'f-industry', 'f-capability', 'f-oss'].forEach(function(id) {{
        var el = document.getElementById(id);
        if (el) el.value = '';
      }});
      var t = document.getElementById('f-traction');
      if (t) t.checked = false;
      onChange();
    }});
  }}
}})();
</script>
</body>
</html>
"""


def render(
    coverage: BatchCoverage,
    companies: list[RawCompany],
    output_path: Path,
    *,
    analyses: list[CompanyAnalysis] | None = None,
    broken_link_count: int = 0,
    allowed_dead_links: bool = False,
    write_company_pages: bool = True,
) -> Path:
    """Render the dashboard. ``analyses`` triggers enriched-mode charts.

    When ``analyses`` is provided and ``write_company_pages`` is True (the
    default), per-company static pages are also written under
    ``output_path.parent / "companies"``. Set ``write_company_pages=False``
    if you only want the main dashboard regenerated (for example, during a
    test run or when iterating on dashboard.py).
    """
    if coverage.coverage_pct_of_official is not None:
        headline_pct = coverage.coverage_pct_of_official
        denominator = coverage.yc_official_count
    else:
        headline_pct = coverage.coverage_pct_of_upstream
        denominator = coverage.upstream_company_count

    analysis_html, analysis_options = _analysis_section(analyses, companies, coverage)
    yc_extra_html, yc_options = _yc_extra_charts(coverage, companies)
    all_options = {**analysis_options, **yc_options}

    company_rows: list[dict[str, Any]] = []
    filter_bar_html = ""
    if analyses:
        company_rows = _build_company_rows(analyses, companies)
        filter_bar_html = _filter_bar(company_rows)

    methodology_lines = [
        f"Data fetched from {coverage.source} (last upstream refresh: {coverage.source_last_updated}).",
        "yc-oss/api is the only sanctioned source per "
        '<a href="https://github.com/RyanAlberts/yc-ai-pulse/blob/main/docs/decisions/0001-yc-data-source.md">'
        "ADR 0001</a>.",
        "Tier A = required fields present and website returned 2xx/3xx.",
        "Tier B = required fields present, website unreachable.",
        "Tier C = required field missing; excluded from charts and listed individually above.",
    ]
    if analyses:
        n_high = sum(1 for a in analyses if a.confidence == "high")
        n_med = sum(1 for a in analyses if a.confidence == "medium")
        n_low = sum(1 for a in analyses if a.confidence == "low")
        methodology_lines.append(
            f"LLM-derived charts use {n_high} high + {n_med} medium-confidence companies "
            f"({n_low} low-confidence rows excluded). Each company sent to a Sonnet model "
            "via the configured backend with a strict pydantic schema; sources must be from "
            "the company's website, YC profile, or pages reached via a polite depth=1 crawl."
        )
    methodology = " ".join(methodology_lines)

    raw = {
        "batch_slug": coverage.batch_slug,
        "fetched_at": coverage.fetched_at.isoformat(),
        "tier_a": coverage.tier_a_count,
        "tier_b": coverage.tier_b_count,
        "tier_c": coverage.tier_c_count,
        "enriched": analyses is not None,
        "analysis_count": len(analyses) if analyses else 0,
    }

    html = DASHBOARD_TEMPLATE.format(
        batch_label=coverage.batch_label,
        batch_slug=coverage.batch_slug,
        source=coverage.source,
        fetched_at=coverage.fetched_at.strftime("%Y-%m-%d %H:%M UTC"),
        headline_pct=headline_pct,
        denominator=denominator,
        analyzable_count=coverage.analyzable_count,
        tier_a=coverage.tier_a_count,
        tier_b=coverage.tier_b_count,
        tier_c=coverage.tier_c_count,
        coverage_alert=_coverage_alert(coverage),
        link_verify_banner=_link_verify_banner(broken_link_count, allowed_dead_links),
        filter_bar=filter_bar_html,
        analysis_section=analysis_html,
        yc_extra_section=yc_extra_html,
        dropped_table=_dropped_table(coverage),
        methodology_text=methodology,
        raw_data_json=_escape(json.dumps(raw, default=str)),
        echarts_cdn=ECHARTS_CDN,
        echarts_sri=ECHARTS_SRI,
        echarts_version=ECHARTS_VERSION,
        # JSON inside <script type="application/json"> is raw text, not HTML.
        # The only escape needed is to prevent a literal "</script" inside the
        # JSON from closing the script tag prematurely.
        chart_options_json=json.dumps(all_options).replace("</script", r"<\/script"),
        companies_json=json.dumps(company_rows).replace("</script", r"<\/script"),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)

    if analyses and write_company_pages:
        # Lazy import to avoid a hard dependency cycle if dashboard_company
        # ever needs to import from dashboard.
        from ycai.dashboard_company import render_company_pages

        render_company_pages(coverage, companies, analyses, output_dir=output_path.parent)

    return output_path


# ----- publish-gate helpers ------------------------------------------------------------


def collect_cited_urls(analyses: Iterable[CompanyAnalysis]) -> list[str]:
    """Every URL that appears in any cited source. The publish gate verifies these."""
    urls: list[str] = []
    seen: set[str] = set()
    for a in analyses:
        for src in a.sources:
            url = str(src)
            if url not in seen:
                seen.add(url)
                urls.append(url)
        if a.oss_evidence_url:
            evidence = str(a.oss_evidence_url)
            if evidence not in seen:
                seen.add(evidence)
                urls.append(evidence)
    return urls


def write_broken_links_report(
    output_dir: Path,
    broken: dict[str, tuple[str, str]],
    analyses: list[CompanyAnalysis],
) -> Path:
    """Sidecar BROKEN_LINKS.md alongside the dashboard."""
    cited_by: dict[str, list[str]] = defaultdict(list)
    for a in analyses:
        for src in a.sources:
            cited_by[str(src)].append(a.slug)
        if a.oss_evidence_url:
            cited_by[str(a.oss_evidence_url)].append(a.slug)

    lines = ["# BROKEN_LINKS", "", f"{len(broken)} cited URL(s) returned 4xx/5xx at publish time.", ""]
    for url, (status, reason) in broken.items():
        slugs = cited_by.get(url, [])
        slug_list = ", ".join(f"`{s}`" for s in slugs) if slugs else "(no slug)"
        lines.append(f"- {url}")
        lines.append(f"  - status: {status}")
        lines.append(f"  - reason: {reason}")
        lines.append(f"  - cited by: {slug_list}")
        lines.append("")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "BROKEN_LINKS.md"
    path.write_text("\n".join(lines))
    return path


__all__ = [
    "ECHARTS_CDN",
    "ECHARTS_SRI",
    "ECHARTS_VERSION",
    "collect_cited_urls",
    "render",
    "write_broken_links_report",
]
