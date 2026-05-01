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

[Unreleased]: https://github.com/RyanAlberts/yc-ai-pulse/compare/main...HEAD
