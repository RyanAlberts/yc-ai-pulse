"""Per-company static pages: a browsable atlas of every Tier-A/B company.

The main dashboard answers cohort questions ("how many agents?", "what
share of B2B SaaS?"). It does not let a reader land on one company and
see everything we know about that company. This module fixes that.

For every Tier-A/B company in the cohort we render:
- ``companies/index.html`` — a single page listing every company with
  a one-line summary, sortable + searchable client-side. The natural
  landing page when a reader wants to browse rather than filter.
- ``companies/<slug>.html`` — a full per-company page with the LLM
  analysis, traction signals (with source links), tagline, capabilities,
  tech stack, OSS posture and evidence URL, and a "siblings in this
  industry" section that links to other companies in the same primary
  industry.

Pure data plumbing — no LLM calls, no network. Every value rendered
here is already validated by Layer 1 + Layer 2 in the build that
produced ``analyses.jsonl``.

Output is HTML-escaped at every interpolation site (URLs, taglines,
detail strings) to defend against any value that slipped past the
sanitizer. The same ``_escape`` helper the main dashboard uses.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ycai.schemas import (
    BatchCoverage,
    CompanyAnalysis,
    RawCompany,
)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_SHARED_STYLES = """
  :root {
    --bg: #F5F1E8;
    --fg: #1B1B1B;
    --muted: #6B6B6B;
    --accent: #D24E01;
    --line: #DDD8CB;
    --ok: #15803D;
    --warn: #B45309;
    --bad: #B91C1C;
  }
  body { background: var(--bg); color: var(--fg);
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Source Serif Pro", Georgia, serif;
    margin: 0; padding: 0; }
  .wrap { max-width: 920px; margin: 32px auto; padding: 0 24px; }
  h1 { font-size: 32px; margin: 0 0 4px; }
  h2 { font-size: 18px; margin: 32px 0 12px; }
  h3 { font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted);
    margin: 20px 0 8px; }
  .subtitle { color: var(--muted); margin: 0 0 24px; }
  .nav { font-size: 14px; margin: 0 0 24px; }
  .nav a { color: var(--accent); text-decoration: none; }
  .nav a:hover { text-decoration: underline; }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px;
    background: #FFF; border: 1px solid var(--line); margin: 0 4px 4px 0; }
  .pill-accent { background: #FED7AA; border-color: var(--accent); color: #7C2D12; }
  .pill-ok { background: #DCFCE7; border-color: var(--ok); color: #14532D; }
  .pill-warn { background: #FEF3C7; border-color: var(--warn); color: #78350F; }
  .pill-bad { background: #FEE2E2; border-color: var(--bad); color: #7F1D1D; }
  .card { background: white; border: 1px solid var(--line); padding: 20px 24px; margin: 0 0 16px;
    border-radius: 4px; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0 16px; font-size: 14px; }
  th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left;
    vertical-align: top; }
  th { background: #FAFAFA; font-weight: 600; font-size: 13px; text-transform: uppercase;
    letter-spacing: 0.4px; color: var(--muted); }
  td.detail { font-size: 13px; }
  td.detail a { color: var(--accent); }
  .quote { font-style: italic; color: #444; }
  .meta-row { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
  .meta-row .item { background: #FFF; border: 1px solid var(--line); padding: 8px 14px;
    border-radius: 4px; min-width: 120px; }
  .meta-row .item .label { color: var(--muted); font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.4px; }
  .meta-row .item .value { font-weight: 600; margin-top: 2px; }
  .source-list { font-size: 13px; }
  .source-list li { word-break: break-all; }
  footer { color: var(--muted); font-size: 13px; margin: 48px 0 24px;
    padding-top: 24px; border-top: 1px solid var(--line); }
  input[type=search] { width: 100%; max-width: 400px; padding: 8px 12px;
    border: 1px solid var(--line); border-radius: 4px; font-size: 15px; }
  .siblings { font-size: 13px; }
  .siblings a { color: var(--accent); margin-right: 8px; }
"""


# ----- per-company page --------------------------------------------------------


def _confidence_pill(confidence: str) -> str:
    cls = {"high": "pill-ok", "medium": "pill-warn", "low": "pill-bad"}.get(confidence, "")
    return f'<span class="pill {cls}">confidence: {_escape(confidence)}</span>'


def _oss_pill(posture: str) -> str:
    cls = "pill-ok" if posture in ("fully-open", "weights-only", "source-available") else "pill-warn"
    if posture in ("closed", "unknown"):
        cls = "pill"
    return f'<span class="pill {cls}">{_escape(posture)}</span>'


def _format_pills(items: Iterable[str], *, accent: bool = False) -> str:
    cls = "pill pill-accent" if accent else "pill"
    return "".join(f'<span class="{cls}">{_escape(s)}</span>' for s in items)


def _traction_table(analysis: CompanyAnalysis) -> str:
    if not analysis.traction:
        return '<p style="color: var(--muted);">No verifiable traction signals on the public surfaces we crawled.</p>'
    rows = [
        f"<tr>"
        f'<td><span class="pill">{_escape(t.kind.value)}</span></td>'
        f'<td class="detail quote">{_escape(t.detail)}</td>'
        f'<td class="detail"><a href="{_escape(str(t.source_url))}">source</a></td>'
        f"</tr>"
        for t in analysis.traction
    ]
    return (
        "<table><thead><tr><th>Kind</th><th>Detail (verbatim)</th><th>Link</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _siblings_block(slug: str, industry_value: str, peers: list[CompanyAnalysis]) -> str:
    """Show up to 12 other companies in the same primary industry."""
    others = [p for p in peers if p.slug != slug and p.industry_primary.value == industry_value]
    if not others:
        return ""
    others = sorted(others, key=lambda a: a.slug)[:12]
    links = " ".join(f'<a href="{_escape(p.slug)}.html"><code>{_escape(p.slug)}</code></a>' for p in others)
    return f"<h3>Other {_escape(industry_value)} companies in this batch</h3>" f'<p class="siblings">{links}</p>'


_COMPANY_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{name} ({slug}) — yc-ai-pulse</title>
<style>{styles}</style>
</head>
<body>
<div class="wrap">
  <p class="nav">
    <a href="../dashboard.html">← Cohort dashboard</a> ·
    <a href="index.html">Browse all companies</a>
  </p>
  <h1>{name}</h1>
  <p class="subtitle">YC slug <code>{slug}</code> · {batch_label}</p>

  <div class="card">
    <p class="quote" style="margin: 0;">{tagline}</p>
  </div>

  <div class="meta-row">
    <div class="item"><div class="label">Industry</div><div class="value">{industry}</div></div>
    <div class="item"><div class="label">OSS posture</div><div class="value">{oss_pill}</div></div>
    <div class="item"><div class="label">Confidence</div><div class="value">{confidence_pill}</div></div>
  </div>

  <h2>Capabilities</h2>
  <p>{capabilities_pills}</p>

  {industry_secondary_block}

  <h2>Tech stack</h2>
  <p>{tech_stack_pills}</p>

  {oss_evidence_block}

  <h2>Traction signals</h2>
  {traction_table}

  <h2>Sources</h2>
  <ul class="source-list">
    {source_list}
  </ul>

  {rationale_block}

  {siblings_block}

  <footer>
    <p>This page is generated from <code>analyses.jsonl</code> by yc-ai-pulse.
    Every field above is the LLM's classification with sources cited; nothing is invented.
    See <a href="../dashboard.html#dropped">the dropped register</a> for excluded companies.</p>
    <p style="margin-top: 8px;">
    Source code: <a href="https://github.com/RyanAlberts/yc-ai-pulse">github.com/RyanAlberts/yc-ai-pulse</a>
    </p>
  </footer>
</div>
</body>
</html>
"""


def render_company_page(
    analysis: CompanyAnalysis,
    company: RawCompany | None,
    *,
    coverage: BatchCoverage,
    peers: list[CompanyAnalysis],
    output_path: Path,
) -> Path:
    """Render one company's static page."""
    name = (company.name if company else analysis.slug) or analysis.slug
    industry_value = analysis.industry_primary.value
    secondary = [i.value for i in analysis.industry_secondary]
    industry_secondary_block = f"<h3>Secondary industries</h3><p>{_format_pills(secondary)}</p>" if secondary else ""
    capabilities = [c.value for c in analysis.ai_capability]
    tech_stack = [s.value for s in analysis.tech_stack] or ["unknown"]
    oss_evidence_block = ""
    if analysis.oss_evidence_url:
        url = str(analysis.oss_evidence_url)
        oss_evidence_block = f'<h3>OSS evidence</h3><p><a href="{_escape(url)}">{_escape(url)}</a></p>'
    rationale_block = (
        f'<h3>Why this classification</h3><p class="quote">{_escape(analysis.rationale)}</p>'
        if analysis.rationale
        else ""
    )
    sources_html = "".join(f'<li><a href="{_escape(str(s))}">{_escape(str(s))}</a></li>' for s in analysis.sources)
    html = _COMPANY_TEMPLATE.format(
        styles=_SHARED_STYLES,
        slug=_escape(analysis.slug),
        name=_escape(name),
        batch_label=_escape(coverage.batch_label),
        tagline=_escape(analysis.tagline_rewrite),
        industry=_escape(industry_value),
        oss_pill=_oss_pill(analysis.oss_posture.value),
        confidence_pill=_confidence_pill(analysis.confidence),
        capabilities_pills=_format_pills(capabilities, accent=True),
        industry_secondary_block=industry_secondary_block,
        tech_stack_pills=_format_pills(tech_stack),
        oss_evidence_block=oss_evidence_block,
        traction_table=_traction_table(analysis),
        source_list=sources_html,
        rationale_block=rationale_block,
        siblings_block=_siblings_block(analysis.slug, industry_value, peers),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path


# ----- index page (browseable atlas) -----------------------------------------


_INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{batch_label} companies — yc-ai-pulse</title>
<style>{styles}
  .index-controls {{ display: flex; gap: 12px; align-items: center; margin: 16px 0 8px;
    flex-wrap: wrap; }}
  .index-table tbody tr {{ cursor: pointer; }}
  .index-table tbody tr:hover {{ background: #FFF7ED; }}
  .index-count {{ color: var(--muted); font-size: 13px; margin-left: auto; }}
  .index-table td:first-child {{ width: 18%; }}
  .index-table td:nth-child(2) {{ width: 22%; }}
</style>
</head>
<body>
<div class="wrap">
  <p class="nav">
    <a href="../dashboard.html">← Cohort dashboard</a>
  </p>
  <h1>{batch_label} — every analyzed company</h1>
  <p class="subtitle">
    {count} companies passed the data-quality gate (Tier A + B) in this batch.
    Click any row for the full classification.
  </p>

  <div class="index-controls">
    <input type="search" id="index-filter" placeholder="Filter by slug, name, or tagline…"
      aria-label="Filter companies" />
    <span class="index-count" id="index-count">{count} of {count}</span>
  </div>

  <table class="index-table">
    <thead>
      <tr>
        <th>Slug</th>
        <th>Name</th>
        <th>Industry</th>
        <th>Capabilities</th>
        <th>OSS</th>
        <th>Confidence</th>
      </tr>
    </thead>
    <tbody id="index-rows">
      {rows}
    </tbody>
  </table>

  <footer>
    <p>Want to filter by industry, capability, or OSS posture and see live charts?
    Use the <a href="../dashboard.html">cohort dashboard</a> filter bar.</p>
  </footer>
</div>
<script>
(function() {{
  var input = document.getElementById('index-filter');
  var rows = document.querySelectorAll('#index-rows tr');
  var count = document.getElementById('index-count');
  var total = rows.length;
  function apply() {{
    var q = input.value.trim().toLowerCase();
    var visible = 0;
    rows.forEach(function(r) {{
      var text = r.dataset.search || '';
      var match = !q || text.indexOf(q) !== -1;
      r.style.display = match ? '' : 'none';
      if (match) visible++;
    }});
    count.textContent = visible + ' of ' + total;
  }}
  input.addEventListener('input', apply);
  rows.forEach(function(r) {{
    r.addEventListener('click', function() {{
      var slug = r.dataset.slug;
      if (slug) window.location.href = slug + '.html';
    }});
  }});
}})();
</script>
</body>
</html>
"""


def render_company_index(
    analyses: list[CompanyAnalysis],
    companies: list[RawCompany],
    *,
    coverage: BatchCoverage,
    output_path: Path,
) -> Path:
    """Render the browseable atlas of all Tier-A/B companies."""
    name_by_slug = {c.slug: c.name for c in companies}
    keep = [a for a in analyses if a.confidence in ("high", "medium")]
    keep_sorted = sorted(keep, key=lambda a: a.slug)

    row_html: list[str] = []
    for a in keep_sorted:
        name = name_by_slug.get(a.slug, a.slug)
        caps = ", ".join(c.value for c in a.ai_capability[:3])
        if len(a.ai_capability) > 3:
            caps += f" +{len(a.ai_capability) - 3}"
        search_blob = " ".join([a.slug, name, a.tagline_rewrite, a.industry_primary.value, caps]).lower()
        row_html.append(
            f'<tr data-slug="{_escape(a.slug)}" data-search="{_escape(search_blob)}">'
            f"<td><code>{_escape(a.slug)}</code></td>"
            f"<td>{_escape(name)}</td>"
            f"<td>{_escape(a.industry_primary.value)}</td>"
            f'<td><span class="pill pill-accent">{_escape(caps)}</span></td>'
            f"<td>{_oss_pill(a.oss_posture.value)}</td>"
            f"<td>{_confidence_pill(a.confidence)}</td>"
            f"</tr>"
        )
    html = _INDEX_TEMPLATE.format(
        styles=_SHARED_STYLES,
        batch_label=_escape(coverage.batch_label),
        count=len(keep),
        rows="\n      ".join(row_html),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path


# ----- public driver --------------------------------------------------------


def render_company_pages(
    coverage: BatchCoverage,
    companies: list[RawCompany],
    analyses: list[CompanyAnalysis],
    *,
    output_dir: Path,
) -> tuple[Path, list[Path]]:
    """Render the index + every per-company page into ``output_dir/companies/``.

    Returns ``(index_path, [company_paths])``. Companies excluded from charts
    (low confidence, Tier-C) do NOT get pages — they're documented in the
    main dashboard's dropped register.
    """
    company_dir = output_dir / "companies"
    company_dir.mkdir(parents=True, exist_ok=True)
    index_path = render_company_index(analyses, companies, coverage=coverage, output_path=company_dir / "index.html")
    company_by_slug = {c.slug: c for c in companies}
    keep = [a for a in analyses if a.confidence in ("high", "medium")]
    pages: list[Path] = []
    for a in keep:
        page_path = company_dir / f"{a.slug}.html"
        render_company_page(
            a,
            company_by_slug.get(a.slug),
            coverage=coverage,
            peers=keep,
            output_path=page_path,
        )
        pages.append(page_path)
    return index_path, pages


__all__ = [
    "render_company_index",
    "render_company_page",
    "render_company_pages",
]
