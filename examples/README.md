# Example outputs

Sanitized sample artifacts. Every commit goes through `make publish-check` so PII can't slip in.

| File | What |
|---|---|
| [`output/dashboard-w26-pr11-2026-05-01.html`](output/dashboard-w26-pr11-2026-05-01.html) | **PR #11 dashboard — current best.** Depth=1 crawl enabled. OSS posture `unknown` rate dropped 55%→21% on the same W26 cohort. Tech-stack identified mentions 14→41. |
| [`output/analyses-w26-pr11-2026-05-01.json`](output/analyses-w26-pr11-2026-05-01.json) | PR #11 enrichment with crawled context. 113/124 high-confidence. |
| [`output/dashboard-w26-pr4-2026-05-01.html`](output/dashboard-w26-pr4-2026-05-01.html) | PR #4 / v0.1.0 dashboard. Useful baseline to compare against PR #11 (no crawl, 65 OSS-unknown rows). |
| [`output/analyses-w26-pr4-2026-05-01.json`](output/analyses-w26-pr4-2026-05-01.json) | **PR #4 enrichment.** 124 companies, 0 schema failures, 6 genuine model lows, 0 hallucinated source URLs. |
| [`output/dashboard-w26-enriched-2026-05-01.html`](output/dashboard-w26-enriched-2026-05-01.html) | PR #3 dashboard, kept as before/after comparison (67% high-confidence, 23% schema failures). |
| [`output/dashboard-w26-2026-05-01.html`](output/dashboard-w26-2026-05-01.html) | PR #1 baseline (coverage-only mode, no LLM). |
| [`output/coverage-w26-2026-05-01.json`](output/coverage-w26-2026-05-01.json) | Machine-readable coverage report — what feeds the dashboard. |
| [`output/analyses-w26-full-2026-05-01.json`](output/analyses-w26-full-2026-05-01.json) | PR #3 full-batch enrichment. Kept for comparison. |
| [`output/analyses-w26-smoke-2026-05-01.json`](output/analyses-w26-smoke-2026-05-01.json) | PR #2 smoke run: 5 companies, the original proof of life. |
| [`output/BROKEN_LINKS-w26-2026-05-01.md`](output/BROKEN_LINKS-w26-2026-05-01.md) | Sidecar from the PR #3 full run. Names dead cited URLs and the slugs that cited them. |

The full quality writeup for W26 is in [`docs/QUALITY_REPORT_W26.md`](../docs/QUALITY_REPORT_W26.md).

Phase 2 will add `deck.pptx` and `report.docx` examples here.
