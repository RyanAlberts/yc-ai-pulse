# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Phase 0 bootstrap: MIT license, repo scaffolding, pre-commit + secret-scan, CI workflow, BACKLOG discipline, first ADR.
- Phase 1 PR #1: yc-oss/api scraper, PII sanitizer, link verifier, coverage probe, single-file dashboard, Typer CLI (`ycai run-coverage`).
- Coverage metric is the dashboard headline. The dropped register acknowledges every excluded company and the specific reason — no quiet drops.
- First end-to-end probe on YC W26: 63.3% coverage of the official 196-company batch. Findings in `docs/QUALITY_REPORT_W26.md`.
- Phase 1 PR #2: LLM-based enrichment with anti-hallucination Layer 1 — pydantic-enforced output schema, source-URL guard against fabricated citations, two-pass cross-check on uncertain rows, sentinel low-confidence row on any failure. Three backends: `AgentSDKBackend` (subscription-default), `AnthropicAPIBackend` (`--api-key`), `MockBackend` (tests). 10 hallucination-trap fixtures locked in as regression tests.
- W26 enrichment smoke run (5 companies via subscription, 39s, ~free): 4 high / 1 low confidence. Identified `gru.space` as `no-ai` correctly. Schema-validation failure on `velum-labs` correctly fell through to the sentinel — no fabricated analysis served.
- Phase 1 PR #3: enriched dashboard. AI capability x industry heatmap, tech-stack distribution, OSS-posture breakdown, and confidence breakdown — all with row-level drill-downs. Cited-URL link-verify hard gate before any artifact ships (override via `--allow-dead-links` writes a `BROKEN_LINKS.md` sidecar and shows a warning banner). Lenient parsing for `industry_secondary` so the model can emit reasonable categories without tanking the row.
- W26 full-batch enrichment via subscription (124 companies, ~6 min, ~free): 83 high / 41 low confidence. Top finding: **65% of high-confidence W26 companies (54 of 83) build agents**. 8 companies correctly classified as `no-ai` (the trust signal). 3 cited URLs caught dead at publish time and surfaced via the publish gate.
- Phase 1 PR #4: resilience + parser tightening. Lenient parsing extended to `ai_capability` (drop unknowns, fall back to `unclear`) and `tech_stack` (drop unknowns). `rationale` and `tagline_rewrite` truncate at the schema cap rather than fail the whole row. Raw failure capture (`raw_failures.jsonl`) for audit. Incremental writes to `analyses.jsonl` so partial state survives a crash. New `ycai resume <run-dir>` command resumes interrupted enrichment. New `ycai dashboard <run-dir>` re-renders the dashboard from existing artifacts at zero LLM cost. Live progress shows high/medium/low counts during enrichment.
- W26 full-batch re-run after PR #4: schema-validation failure rate dropped from 23% to **0%**. High-confidence rate went from 67% to **95%** (118 of 124). The "W26 is the agentic batch" finding strengthened to 58% of n=118. Quality writeup updated at `docs/QUALITY_REPORT_W26.md`.

[Unreleased]: https://github.com/RyanAlberts/yc-ai-pulse/compare/main...HEAD
