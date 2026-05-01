"""Render the dashboard HTML. Headline metric is coverage; the dropped
register is rendered before any chart so quality issues are unmissable.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ycai.schemas import BatchCoverage, CoverageTier, RawCompany

DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{batch_label} — yc-ai-pulse coverage report</title>
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
  .bar-row .count {{ flex: 0 0 60px; text-align: right; color: var(--muted); }}
  footer {{ color: var(--muted); font-size: 13px; margin: 48px 0 24px; padding-top: 24px; border-top: 1px solid var(--line); }}
  .alert {{ background: #FFF7ED; border-left: 4px solid var(--warn); padding: 14px 18px; margin: 16px 0; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: 500; background: #EEE; color: #555; }}
  .badge.tier-A {{ background: #DCFCE7; color: #15803D; }}
  .badge.tier-B {{ background: #FEF3C7; color: #B45309; }}
  .badge.tier-C {{ background: #FEE2E2; color: #B91C1C; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>The State of AI in {batch_label}</h1>
  <p class="subtitle">yc-ai-pulse coverage report · generated {fetched_at} · source {source}</p>

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

  <div class="grid">
    <div class="stat"><div class="num ok">{tier_a}</div><div class="label">Tier A · full classification</div></div>
    <div class="stat"><div class="num warn">{tier_b}</div><div class="label">Tier B · partial (website unreachable)</div></div>
    <div class="stat"><div class="num bad">{tier_c}</div><div class="label">Tier C · excluded</div></div>
  </div>

  <div class="chart-card">
    <h2>Industry distribution (Tier A + B only)</h2>
    {industry_chart}
    <details>
      <summary>See source rows ({analyzable_count} companies)</summary>
      <table>
        <thead><tr><th>Slug</th><th>Name</th><th>Industry</th><th>Tier</th></tr></thead>
        <tbody>{industry_rows}</tbody>
      </table>
    </details>
  </div>

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
    Run <code>ycai run --batch {batch_slug}</code> to regenerate.
    </p>
  </footer>

  <script type="application/json" id="raw-data">{raw_data_json}</script>
</div>
</body>
</html>
"""


def _bar_chart(counter: Counter[str], total: int, top: int = 12) -> str:
    """Render a horizontal CSS bar chart. No JS, no CDN — works offline."""
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


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _coverage_alert(coverage: BatchCoverage) -> str:
    """Render an upfront alert banner for any meaningful gap."""
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
    inner = "<br/>".join(notes)
    return f'<div class="alert">{inner}</div>'


def _dropped_table(coverage: BatchCoverage) -> str:
    drops = [r for r in coverage.records if r.tier == CoverageTier.C]
    if not drops:
        return '<p style="color: var(--muted);">None — every upstream company met the data-quality bar.</p>'
    rows: list[str] = []
    for record in sorted(drops, key=lambda r: r.slug):
        reasons = ", ".join(r.value for r in record.drop_reasons)
        rows.append(
            f"<tr><td><code>{_escape(record.slug)}</code></td>"
            f"<td>{_escape(record.name)}</td>"
            f'<td class="reason">{_escape(reasons)}</td></tr>'
        )
    return (
        "<table><thead><tr><th>Slug</th><th>Name</th><th>Reasons</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render(
    coverage: BatchCoverage,
    companies: list[RawCompany],
    output_path: Path,
) -> Path:
    """Render the dashboard HTML to ``output_path``. Returns the path."""
    analyzable = {record.slug for record in coverage.records if record.tier in (CoverageTier.A, CoverageTier.B)}
    keepers = [c for c in companies if c.slug in analyzable]

    industries = Counter()
    tags = Counter()
    regions = Counter()
    for c in keepers:
        if c.industry:
            industries[c.industry] += 1
        for t in c.tags:
            tags[t] += 1
        for r in c.regions:
            regions[r] += 1

    industry_rows = "\n".join(
        f"<tr><td><code>{_escape(c.slug)}</code></td>"
        f"<td>{_escape(c.name)}</td>"
        f"<td>{_escape(c.industry)}</td>"
        f'<td><span class="badge tier-A">A</span></td></tr>'
        for c in keepers
    )

    if coverage.coverage_pct_of_official is not None:
        headline_pct = coverage.coverage_pct_of_official
        denominator = coverage.yc_official_count
    else:
        headline_pct = coverage.coverage_pct_of_upstream
        denominator = coverage.upstream_company_count

    methodology = (
        f"Data fetched from {coverage.source} "
        f"(last upstream refresh: {coverage.source_last_updated}). "
        f"yc-oss/api is the only sanctioned source per ADR 0001 — see "
        '<a href="https://github.com/RyanAlberts/yc-ai-pulse/blob/main/docs/decisions/0001-yc-data-source.md">'
        "the ADR</a> for why. "
        f"Tier A = required fields present and website returned 2xx/3xx. "
        f"Tier B = required fields present but website unreachable (analysis flagged in charts). "
        f"Tier C = required field missing — excluded from charts and listed individually above."
    )

    raw = {
        "batch_slug": coverage.batch_slug,
        "fetched_at": coverage.fetched_at.isoformat(),
        "tier_a": coverage.tier_a_count,
        "tier_b": coverage.tier_b_count,
        "tier_c": coverage.tier_c_count,
        "industries": dict(industries),
        "tags": dict(tags),
        "regions": dict(regions),
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
        industry_chart=_bar_chart(industries, coverage.analyzable_count, top=12),
        industry_rows=industry_rows,
        tag_chart=_bar_chart(tags, coverage.analyzable_count, top=20),
        region_chart=_bar_chart(regions, coverage.analyzable_count, top=12),
        dropped_table=_dropped_table(coverage),
        methodology_text=methodology,
        raw_data_json=_escape(json.dumps(raw, default=str)),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path


__all__ = ["render"]
