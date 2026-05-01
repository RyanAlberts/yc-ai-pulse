# Example outputs

Sanitized sample artifacts. Every commit goes through `make publish-check` so PII can't slip in.

| File | What |
|---|---|
| [`output/dashboard-w26-2026-05-01.html`](output/dashboard-w26-2026-05-01.html) | Phase 1 dashboard for YC W26. Headline: 63.3% coverage of the 196-company batch, with the dropped register naming every excluded company. |
| [`output/coverage-w26-2026-05-01.json`](output/coverage-w26-2026-05-01.json) | Machine-readable coverage report — what feeds the dashboard. |
| [`output/analyses-w26-smoke-2026-05-01.json`](output/analyses-w26-smoke-2026-05-01.json) | PR #2 smoke run: 5-company LLM enrichment via Sonnet 4.6 on subscription. Captures the schema-enforced output and demonstrates source-URL grounding (every cited URL is from `website` or YC profile). |

The full quality writeup for W26 is in [`docs/QUALITY_REPORT_W26.md`](../docs/QUALITY_REPORT_W26.md).

Phase 2 will add `deck.pptx` and `report.docx` examples here.
