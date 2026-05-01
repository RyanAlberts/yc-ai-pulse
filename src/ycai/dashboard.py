"""Render the dashboard HTML.

Two modes:
  - coverage-only: PR #1 output. Headline is % batch coverage, charts are
    YC-supplied industry/tags/regions (no LLM).
  - enriched: PR #3 output. Adds AI-capability heatmap, tech-stack chart,
    OSS-posture pie, and confidence breakdown. All driven by analyses.json.

In both modes the dropped register is rendered before any chart so quality
issues are unmissable.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from ycai.schemas import (
    BatchCoverage,
    CompanyAnalysis,
    CoverageTier,
    RawCompany,
)

# OSS-posture color palette — green to red.
_OSS_COLORS: dict[str, str] = {
    "fully-open": "#15803D",
    "weights-only": "#65A30D",
    "source-available": "#84CC16",
    "api-only": "#F59E0B",
    "closed": "#B91C1C",
    "unknown": "#9CA3AF",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ----- chart primitives ---------------------------------------------------------------------


def _bar_chart(counter: Counter[str], total: int, top: int = 12) -> str:
    """Horizontal CSS bar chart. No JS, works offline."""
    if not counter:
        return '<p style="color: var(--muted);">No data.</p>'
    max_count = counter.most_common(1)[0][1]
    rows: list[str] = []
    for name, count in counter.most_common(top):
        pct = (count / max_count) * 100 if max_count else 0
        share = (count / total) * 100 if total else 0
        rows.append(
            f'<div class="bar-row"><div class="name">{_escape(name)}</div>'
            f'<div class="bar" style="width: {pct:.1f}%"></div>'
            f'<div class="count">{count} · {share:.1f}%</div></div>'
        )
    return "\n".join(rows)


def _stacked_bar(counter: Counter[str], total: int, color_map: dict[str, str]) -> str:
    """Single horizontal stacked bar showing share across categories. The
    pie-replacement that's easier to scan than an actual pie."""
    if not counter:
        return '<p style="color: var(--muted);">No data.</p>'
    segments: list[str] = []
    legend_items: list[str] = []
    for name, count in counter.most_common():
        pct = (count / total) * 100 if total else 0
        color = color_map.get(name, "#999")
        segments.append(
            f'<div title="{_escape(name)}: {count} ({pct:.1f}%)" '
            f'style="flex: {pct}; background: {color}; min-width: 1px;"></div>'
        )
        legend_items.append(
            f'<span class="legend-item">'
            f'<span class="legend-swatch" style="background: {color}"></span>'
            f"{_escape(name)} ({count} · {pct:.1f}%)</span>"
        )
    return (
        '<div class="stacked-bar">' + "".join(segments) + "</div>"
        '<div class="legend">' + " ".join(legend_items) + "</div>"
    )


def _heatmap(matrix: dict[tuple[str, str], int], rows: list[str], cols: list[str]) -> str:
    """A 2D heatmap. ``rows`` are y-axis labels (capabilities), ``cols`` are x-axis
    labels (industries). Cell intensity scales to the matrix max."""
    if not matrix:
        return '<p style="color: var(--muted);">No data.</p>'
    max_val = max(matrix.values()) or 1
    out: list[str] = ['<div class="heatmap-wrap"><table class="heatmap"><thead><tr><th></th>']
    for col in cols:
        out.append(f"<th>{_escape(col)}</th>")
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append(f"<tr><th>{_escape(row)}</th>")
        for col in cols:
            count = matrix.get((row, col), 0)
            opacity = (count / max_val) if max_val else 0
            cell_text = str(count) if count else ""
            out.append(
                f'<td style="background: rgba(210, 78, 1, {opacity:.2f});" '
                f'title="{_escape(row)} x {_escape(col)} = {count}">{cell_text}</td>'
            )
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


# ----- chart-card composer (with drill-down) ---------------------------------------------


def _chart_card(
    title: str,
    chart_html: str,
    drill_summary: str | None = None,
    drill_table_html: str | None = None,
) -> str:
    parts = [f'<div class="chart-card"><h2>{_escape(title)}</h2>', chart_html]
    if drill_summary and drill_table_html:
        parts.append(f"<details><summary>{_escape(drill_summary)}</summary>{drill_table_html}</details>")
    parts.append("</div>")
    return "".join(parts)


def _slug_table(rows: list[tuple[str, str, str]]) -> str:
    """Three-column slug/name/value table for drill-downs."""
    body = "".join(
        f"<tr><td><code>{_escape(s)}</code></td><td>{_escape(n)}</td><td>{_escape(v)}</td></tr>" for s, n, v in rows
    )
    return f"<table><thead><tr><th>Slug</th><th>Name</th><th>Value</th></tr></thead><tbody>{body}</tbody></table>"


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


# ----- enriched charts (PR #3) ------------------------------------------------------------


def _confidence_breakdown(analyses: Iterable[CompanyAnalysis]) -> tuple[str, str]:
    by_conf: Counter[str] = Counter(a.confidence for a in analyses)
    total = sum(by_conf.values())
    chart = _stacked_bar(
        by_conf,
        total,
        {"high": "#15803D", "medium": "#F59E0B", "low": "#B91C1C"},
    )
    return chart, f"high: {by_conf['high']} · medium: {by_conf['medium']} · low: {by_conf['low']}"


def _industry_breakdown(analyses: list[CompanyAnalysis]) -> tuple[str, str]:
    """LLM-derived industry distribution. Excludes low-confidence rows."""
    high = [a for a in analyses if a.confidence in ("high", "medium")]
    by_ind: Counter[str] = Counter(a.industry_primary.value for a in high)
    chart = _bar_chart(by_ind, total=len(high), top=12)
    rows = [(a.slug, a.tagline_rewrite[:60], a.industry_primary.value) for a in high]
    drill = _slug_table(rows)
    return chart, drill


def _capability_heatmap(analyses: list[CompanyAnalysis]) -> tuple[str, str]:
    """Capability x industry. Each company contributes 1 to every (capability, industry) cell
    where capability appears in its ai_capability list and industry == industry_primary."""
    keep = [a for a in analyses if a.confidence in ("high", "medium")]
    matrix: dict[tuple[str, str], int] = defaultdict(int)
    for a in keep:
        for cap in a.ai_capability:
            matrix[(cap.value, a.industry_primary.value)] += 1
    # Top capabilities (by total count) on Y, top industries on X.
    cap_totals: Counter[str] = Counter()
    ind_totals: Counter[str] = Counter()
    for (cap_label, ind_label), v in matrix.items():
        cap_totals[cap_label] += v
        ind_totals[ind_label] += v
    top_caps = [name for name, _ in cap_totals.most_common(8)]
    top_inds = [name for name, _ in ind_totals.most_common(6)]
    chart = _heatmap(dict(matrix), top_caps, top_inds)

    rows: list[tuple[str, str, str]] = []
    for a in keep:
        caps = ", ".join(cap.value for cap in a.ai_capability)
        rows.append((a.slug, a.tagline_rewrite[:60], f"{a.industry_primary.value} | {caps}"))
    drill = _slug_table(rows)
    return chart, drill


def _tech_stack_chart(analyses: list[CompanyAnalysis]) -> tuple[str, str]:
    keep = [a for a in analyses if a.confidence in ("high", "medium")]
    by_stack: Counter[str] = Counter()
    for a in keep:
        if not a.tech_stack:
            by_stack["unknown"] += 1
        else:
            for stack in a.tech_stack:
                by_stack[stack.value] += 1
    chart = _bar_chart(by_stack, total=len(keep), top=10)

    rows: list[tuple[str, str, str]] = []
    for a in keep:
        rows.append(
            (
                a.slug,
                a.tagline_rewrite[:60],
                ", ".join(s.value for s in a.tech_stack) or "unknown",
            )
        )
    drill = _slug_table(rows)
    return chart, drill


def _oss_posture_chart(analyses: list[CompanyAnalysis]) -> tuple[str, str]:
    keep = [a for a in analyses if a.confidence in ("high", "medium")]
    by_oss: Counter[str] = Counter(a.oss_posture.value for a in keep)
    chart = _stacked_bar(by_oss, total=len(keep), color_map=_OSS_COLORS)

    rows: list[tuple[str, str, str]] = []
    for a in keep:
        evidence = str(a.oss_evidence_url) if a.oss_evidence_url else "—"
        rows.append((a.slug, a.tagline_rewrite[:60], f"{a.oss_posture.value}  ({evidence})"))
    drill = _slug_table(rows)
    return chart, drill


# ----- main render --------------------------------------------------------------------------


DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
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
  .headline {{ background: white; border: 1px solid var(--line); padding: 24px 28px; margin: 0 0 24px; }}
  .headline-num {{ font-size: 64px; font-weight: 600; letter-spacing: -1px; line-height: 1; color: var(--accent); }}
  .headline-num small {{ font-size: 24px; color: var(--muted); font-weight: 400; }}
  .headline-label {{ color: var(--muted); margin-top: 8px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin: 24px 0; }}
  .stat {{ background: white; border: 1px solid var(--line); padding: 18px 20px; }}
  .stat .num {{ font-size: 32px; font-weight: 600; }}
  .stat .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .ok {{ color: var(--ok); }} .warn {{ color: var(--warn); }} .bad {{ color: var(--bad); }}
  details {{ border: 1px solid var(--line); background: white; padding: 14px 20px; margin: 16px 0; }}
  summary {{ cursor: pointer; font-weight: 500; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
  th {{ background: #FAFAFA; font-weight: 600; }}
  td.reason {{ color: var(--muted); font-family: ui-monospace, monospace; font-size: 12px; }}
  .chart-card {{ background: white; border: 1px solid var(--line); padding: 20px; margin: 16px 0; }}
  .chart-card h2 {{ margin: 0 0 16px; font-size: 18px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 4px 0; font-size: 14px; }}
  .bar-row .name {{ flex: 0 0 240px; color: var(--fg); }}
  .bar-row .bar {{ flex: 1; height: 18px; background: var(--accent); }}
  .bar-row .count {{ flex: 0 0 90px; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }}
  .stacked-bar {{ display: flex; height: 32px; border: 1px solid var(--line); margin: 8px 0; overflow: hidden; }}
  .legend {{ font-size: 13px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
  .legend-swatch {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; }}
  .heatmap-wrap {{ overflow-x: auto; }}
  table.heatmap {{ font-size: 13px; }}
  table.heatmap th {{ background: transparent; font-weight: 500; color: var(--muted); border: 0; padding: 6px 10px; }}
  table.heatmap td {{ text-align: center; font-variant-numeric: tabular-nums; border: 1px solid var(--line); padding: 8px 10px; min-width: 60px; }}
  footer {{ color: var(--muted); font-size: 13px; margin: 48px 0 24px; padding-top: 24px; border-top: 1px solid var(--line); }}
  .alert {{ background: #FFF7ED; border-left: 4px solid var(--warn); padding: 14px 18px; margin: 16px 0; }}
  .alert-bad {{ background: #FEE2E2; border-left-color: var(--bad); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 500; background: #EEE; color: #555; }}
  .badge.tier-A {{ background: #DCFCE7; color: #15803D; }}
  .badge.tier-B {{ background: #FEF3C7; color: #B45309; }}
  .badge.tier-C {{ background: #FEE2E2; color: #B91C1C; }}
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

  {analysis_section}

  <div class="chart-card">
    <h2>YC tag distribution (top 20)</h2>
    {tag_chart}
  </div>

  <div class="chart-card">
    <h2>Region distribution (top 12)</h2>
    {region_chart}
  </div>

  <h2 id="dropped" style="margin-top: 48px;">Dropped register · {tier_c} excluded companies</h2>
  <p style="color: var(--muted); max-width: 70ch;">
    Every company excluded from the analysis above is listed here with the
    specific reason. We do not silently drop data.
  </p>
  {dropped_table}

  <footer>
    <p><strong>Methodology.</strong>
    {methodology_text}
    </p>
    <p><strong>Reproduce this run.</strong>
    Source code: <a href="https://github.com/RyanAlberts/yc-ai-pulse">github.com/RyanAlberts/yc-ai-pulse</a>.
    Run <code>ycai run-coverage --batch {batch_slug} --enrich</code> to regenerate.
    </p>
  </footer>

  <script type="application/json" id="raw-data">{raw_data_json}</script>
</div>
</body>
</html>
"""


def _build_analysis_section(
    analyses: list[CompanyAnalysis] | None,
    companies: list[RawCompany],
    coverage: BatchCoverage,
) -> str:
    """Either the LLM-derived charts (when analyses provided) or the
    YC-supplied industry chart (coverage-only mode)."""
    analyzable = {r.slug for r in coverage.records if r.tier in (CoverageTier.A, CoverageTier.B)}
    keepers = [c for c in companies if c.slug in analyzable]

    if not analyses:
        # Coverage-only mode (PR #1 baseline).
        industries: Counter[str] = Counter()
        for c in keepers:
            if c.industry:
                industries[c.industry] += 1
        ind_chart = _bar_chart(industries, coverage.analyzable_count, top=12)
        rows = [(c.slug, c.name, c.industry) for c in keepers]
        drill = _slug_table(rows)
        return _chart_card(
            "Industry distribution (YC-supplied, no LLM)",
            ind_chart,
            f"See source rows ({coverage.analyzable_count} companies)",
            drill,
        )

    # Enriched mode — 4 LLM-derived charts.
    parts: list[str] = []

    conf_chart, conf_caption = _confidence_breakdown(analyses)
    parts.append(_chart_card(f"Classification confidence — {conf_caption}", conf_chart))

    ind_chart, ind_drill = _industry_breakdown(analyses)
    parts.append(
        _chart_card(
            "Industry distribution (LLM-classified, high+medium confidence only)",
            ind_chart,
            f"See {sum(1 for a in analyses if a.confidence != 'low')} source rows",
            ind_drill,
        )
    )

    cap_chart, cap_drill = _capability_heatmap(analyses)
    parts.append(
        _chart_card(
            "AI capability x industry heatmap",
            cap_chart,
            "See per-company capability list",
            cap_drill,
        )
    )

    stack_chart, stack_drill = _tech_stack_chart(analyses)
    parts.append(
        _chart_card(
            "Tech stack signals (where determinable from public surfaces)",
            stack_chart,
            "See per-company stack",
            stack_drill,
        )
    )

    oss_chart, oss_drill = _oss_posture_chart(analyses)
    parts.append(
        _chart_card(
            "Open-source posture",
            oss_chart,
            "See per-company posture + evidence URL",
            oss_drill,
        )
    )

    return "\n".join(parts)


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
    # Should not reach here — pipeline aborts before write — but keep the banner for safety.
    return f'<div class="alert alert-bad"><strong>{broken_count} dead links detected.</strong></div>'


def render(
    coverage: BatchCoverage,
    companies: list[RawCompany],
    output_path: Path,
    *,
    analyses: list[CompanyAnalysis] | None = None,
    broken_link_count: int = 0,
    allowed_dead_links: bool = False,
) -> Path:
    """Render the dashboard. ``analyses`` triggers enriched-mode charts."""
    if coverage.coverage_pct_of_official is not None:
        headline_pct = coverage.coverage_pct_of_official
        denominator = coverage.yc_official_count
    else:
        headline_pct = coverage.coverage_pct_of_upstream
        denominator = coverage.upstream_company_count

    analyzable = {r.slug for r in coverage.records if r.tier in (CoverageTier.A, CoverageTier.B)}
    keepers = [c for c in companies if c.slug in analyzable]
    tags: Counter[str] = Counter()
    regions: Counter[str] = Counter()
    for c in keepers:
        for t in c.tags:
            tags[t] += 1
        for r in c.regions:
            regions[r] += 1

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
            f"({n_low} low-confidence rows excluded). Each company sent to "
            f"{_first_url(analyses)} via the configured backend with a strict pydantic "
            "schema; sources must be from the company's website or YC profile."
        )
    methodology = " ".join(methodology_lines)

    raw = {
        "batch_slug": coverage.batch_slug,
        "fetched_at": coverage.fetched_at.isoformat(),
        "tier_a": coverage.tier_a_count,
        "tier_b": coverage.tier_b_count,
        "tier_c": coverage.tier_c_count,
        "tags": dict(tags),
        "regions": dict(regions),
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
        analysis_section=_build_analysis_section(analyses, companies, coverage),
        tag_chart=_bar_chart(tags, coverage.analyzable_count, top=20),
        region_chart=_bar_chart(regions, coverage.analyzable_count, top=12),
        dropped_table=_dropped_table(coverage),
        methodology_text=methodology,
        raw_data_json=_escape(json.dumps(raw, default=str)),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path


def _first_url(analyses: list[CompanyAnalysis]) -> str:
    """Best-effort: return the model identifier from the first analysis if available.

    Used in methodology text. Currently the schema doesn't carry the model
    name on each row (it's a per-run constant), so we just say 'a Sonnet model'.
    """
    return "a Sonnet model"


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
    """Write a sidecar BROKEN_LINKS.md alongside the dashboard.

    Maps each dead URL to the company slug that cited it, so the user can
    re-run targeted enrichment on the affected rows.
    """
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
    "collect_cited_urls",
    "render",
    "write_broken_links_report",
]
