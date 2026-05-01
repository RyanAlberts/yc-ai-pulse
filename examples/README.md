# Example outputs

Sanitized sample artifacts. Every commit goes through `make publish-check` so PII can't slip in.

| File | What |
|---|---|
| [`output/dashboard-w26-enriched-2026-05-01.html`](output/dashboard-w26-enriched-2026-05-01.html) | **PR #3 full-batch dashboard.** Headline: 63.3% coverage of W26, with LLM-derived charts: AI capability x industry heatmap, tech-stack distribution, OSS-posture breakdown. Dead-link banner at top because 3 cited URLs returned 4xx/5xx at publish time. |
| [`output/dashboard-w26-2026-05-01.html`](output/dashboard-w26-2026-05-01.html) | PR #1 baseline (coverage-only mode, no LLM). Useful comparison for what shifts when --enrich is added. |
| [`output/coverage-w26-2026-05-01.json`](output/coverage-w26-2026-05-01.json) | Machine-readable coverage report — what feeds the dashboard. |
| [`output/analyses-w26-full-2026-05-01.json`](output/analyses-w26-full-2026-05-01.json) | **PR #3 full-batch enrichment.** 124 companies × Sonnet 4.6, ~6 min on subscription. 83 high-confidence rows feed the charts; 41 low-confidence rows surface honestly in the methodology footer. |
| [`output/analyses-w26-smoke-2026-05-01.json`](output/analyses-w26-smoke-2026-05-01.json) | PR #2 smoke run: 5 companies, the original proof of life. |
| [`output/BROKEN_LINKS-w26-2026-05-01.md`](output/BROKEN_LINKS-w26-2026-05-01.md) | Sidecar from the full run. Names the 3 cited URLs that returned 4xx/5xx and the slugs that cited them. |

The full quality writeup for W26 is in [`docs/QUALITY_REPORT_W26.md`](../docs/QUALITY_REPORT_W26.md).

Phase 2 will add `deck.pptx` and `report.docx` examples here.
