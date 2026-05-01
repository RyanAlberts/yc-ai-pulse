# Example outputs

Sanitized sample artifacts. Every commit goes through `make publish-check` so PII can't slip in.

| File | What |
|---|---|
| [`output/dashboard-w26-pr12-2026-05-01.html`](output/dashboard-w26-pr12-2026-05-01.html) | **PR #12 dashboard — current best.** Same W26 data as PR #11 (depth=1 crawl, 113/124 high-confidence) but charts upgraded to Apache ECharts: real heatmap, pies with click-to-isolate, bars with axis tooltips. Loads via CDN with SRI; `<noscript>` fallback if JS disabled. |
| [`output/dashboard-w26-pr11-2026-05-01.html`](output/dashboard-w26-pr11-2026-05-01.html) | PR #11 dashboard with the depth=1 crawl but static CSS bars. Useful for comparing visual fidelity vs. PR #12. |
| [`output/analyses-w26-pr11-2026-05-01.json`](output/analyses-w26-pr11-2026-05-01.json) | Source data for both PR #11 and PR #12 dashboards. 113/124 high-confidence. |
| [`output/dashboard-w26-pr4-2026-05-01.html`](output/dashboard-w26-pr4-2026-05-01.html) | PR #4 / v0.1.0 dashboard. Useful baseline (no crawl, 65 OSS-unknown rows; static CSS bars). |
| [`output/analyses-w26-pr4-2026-05-01.json`](output/analyses-w26-pr4-2026-05-01.json) | **PR #4 enrichment.** 124 companies, 0 schema failures, 6 genuine model lows, 0 hallucinated source URLs. |
| [`output/dashboard-w26-enriched-2026-05-01.html`](output/dashboard-w26-enriched-2026-05-01.html) | PR #3 dashboard, kept as before/after comparison (67% high-confidence, 23% schema failures). |
| [`output/dashboard-w26-2026-05-01.html`](output/dashboard-w26-2026-05-01.html) | PR #1 baseline (coverage-only mode, no LLM). |
| [`output/coverage-w26-2026-05-01.json`](output/coverage-w26-2026-05-01.json) | Machine-readable coverage report — what feeds the dashboard. |
| [`output/analyses-w26-full-2026-05-01.json`](output/analyses-w26-full-2026-05-01.json) | PR #3 full-batch enrichment. Kept for comparison. |
| [`output/analyses-w26-smoke-2026-05-01.json`](output/analyses-w26-smoke-2026-05-01.json) | PR #2 smoke run: 5 companies, the original proof of life. |
| [`output/BROKEN_LINKS-w26-2026-05-01.md`](output/BROKEN_LINKS-w26-2026-05-01.md) | Sidecar from the PR #3 full run. Names dead cited URLs and the slugs that cited them. |

The full quality writeup for W26 is in [`docs/QUALITY_REPORT_W26.md`](../docs/QUALITY_REPORT_W26.md).

Phase 2 will add `deck.pptx` and `report.docx` examples here.
