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

[Unreleased]: https://github.com/RyanAlberts/yc-ai-pulse/compare/main...HEAD
