# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_(no changes since 0.1.0)_

## [0.1.0] — 2026-05-01

First publishable release. End-to-end pipeline that pulls the latest YC batch, classifies it with a Sonnet-class model under strict anti-hallucination guards, and renders a single-file HTML dashboard with row-level drill-downs.

### Added

**Phase 0 — bootstrap (PR #6 lineage starts here)**
- MIT license, repo scaffolding, pre-commit + secret-scan + gitleaks + custom Anthropic-key regex, CI workflow, BACKLOG discipline, first two ADRs (yc-oss/api as the only sanctioned source; localhost FastAPI deferred to Phase 3).

**Phase 1 — analysis pipeline**
- **PR #6 — coverage probe**: yc-oss/api scraper with hard-fail when upstream is unreachable (no scraping `ycombinator.com/companies?...` per [robots.txt](docs/decisions/0001-yc-data-source.md)). PII sanitizer (idempotent strip before disk and before any LLM call). Async link verifier. Coverage probe with three tiers (A: full / B: website unreachable / C: missing required field) and a dropped register that names every excluded company. Coverage % is the dashboard headline.
- **PR #7 — LLM enrichment with anti-hallucination Layer 1**: pydantic-enforced classification schema, three backends (AgentSDK / Anthropic API / Mock), source-URL grounding (the cited URL must come from the company's website or YC profile), two-pass cross-check on medium-confidence rows, sentinel low-confidence row on any failure. 10 hallucination-trap fixtures as regression tests.
- **PR #8 — enriched dashboard + cited-URL publish gate**: capability×industry heatmap, tech-stack distribution, OSS-posture breakdown, confidence breakdown. Each chart drills down to source rows. Cited URLs are HEAD/GET-verified before publish; `--allow-dead-links` writes a sidecar `BROKEN_LINKS.md` and surfaces a banner.
- **PR #9 — resilience + parser tightening**: schema-failure rate dropped 23% → 0%. Truncate-not-reject for verbose free-text fields (`rationale`, `tagline_rewrite`). Lenient parsing for `ai_capability` and `tech_stack`. Raw failure capture (`raw_failures.jsonl`). Incremental writes to `analyses.jsonl`. `ycai resume` recovers from interrupted runs. `ycai dashboard` re-renders from existing artifacts at zero LLM cost.

**Real W26 results captured under `examples/output/`:**
- 63.3% coverage of the 196-company batch (132 in upstream, 124 Tier A+B, 8 named drops, 4 Tier B with dead websites)
- 118 of 124 high-confidence (95%) on the LLM enrichment, 0 schema failures, 0 hallucinated source URLs
- **Top finding: 58% of high-confidence W26 companies build agents.** "W26 is the agentic batch" is now defensible with row-level evidence.

### Backlog status at release

| ID | Status | Note |
|---|---|---|
| B001 | resolved | yc-oss/api is sole source; ADR 0001 amended in PR #6 |
| B002 | open | Cloudflare cache-headroom check on `yc-oss.github.io/api/*` |
| B003 | open | Node 20 actions deprecated by 2026-06-02 — bump CI before then |
| B004 | open | Calibrate `MIN_DESCRIPTION_CHARS` against borderline rows |
| B005 | open | Name the missing-from-upstream W26 companies, not just count |
| B006 | resolved | Schema-validation rate measured + tuned in PR #9 |
| B007 | open | Depth=1 website crawl to recover `tech_stack` and `oss_posture` from `unknown` — biggest signal lever for v0.2 |
| B008 | resolved | (rationale-cap root cause shipped in PR #9) |

### Tests

103 tests passing. Mypy `--strict` clean. CI runs ruff, mypy, pytest, detect-secrets, gitleaks, and a custom credential-pattern sweep on every PR.

[Unreleased]: https://github.com/RyanAlberts/yc-ai-pulse/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RyanAlberts/yc-ai-pulse/releases/tag/v0.1.0
