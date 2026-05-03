# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — PR #18 (Phase 3 backend daemon)
- New `src/ycai/server.py`: FastAPI daemon. Endpoints: `GET /healthz` (public), `GET /v1/info`, `GET /v1/companies` (autocomplete source for the extension), `POST /v1/runs`, `GET /v1/runs`, `GET /v1/runs/{id}/status`, `GET /v1/runs/{id}/events` (SSE), `POST /v1/companies/{slug}/deep-dive`. All `/v1/*` endpoints require `Authorization: Bearer <token>`.
- New `src/ycai/daemon.py`: process lifecycle. PID file + token at `~/.ycai/`, mode 0600 on the token. `start()` blocks until `/healthz` answers; `stop()` SIGTERMs then SIGKILLs; `status()` combines PID-file existence with a synchronous health probe.
- New CLI: `ycai daemon start | stop | status | token`. The token print on `start` is the only path that surfaces it on stdout — never logged.
- CORS allowlist: `chrome-extension://*` only. Bind to 127.0.0.1 by default. Per ADR 0002.
- 14 new tests (175 total). ASGI in-process via `httpx.ASGITransport`; no real port binding. Coverage: `/healthz` public, missing/wrong/correct token paths, `POST /v1/runs` lifecycle, deep-dive record kind, `/v1/companies` reads from the latest run dir, registry subscribe/unsubscribe behavior, CORS allows extension origins and ignores others.
- Live smoke verified end-to-end: `ycai daemon start` → `/healthz` returns ok → `/v1/info` requires the token → `ycai daemon stop` cleans up the PID file.

### Added — PR #17 (memo polish)
- **Executive summary** at the top of the memo, citing the Nobel laureate's framing of what the headline finding implies for capital allocation.
- **Three-POV introduction** — single paragraph that pits Marc Andreessen, Ray Dalio, and Daron Acemoglu (2024 Nobel laureate in Economics) against each other on what the batch findings imply. The memo deliberately does not pick a winner. Codified in [`docs/MEMO_STRUCTURE.md`](docs/MEMO_STRUCTURE.md) and [ADR 0003](docs/decisions/0003-three-povs-memo-frame.md).
- **Inside B2B SaaS** sub-industry table — one-layer-deeper breakdown using YC's `subindustry` passthrough (not LLM-derived, so it can't drift). Renders only when B2B SaaS rows exist.
- **Tech stack chart now excludes the `unknown` bucket**; the unknown count is rendered as a footnote / asterisk under the chart instead of as the largest bar.
- **Traction signals section** — companies that advertise verifiable traction (GitHub stars, named customers, funding rounds, revenue, user counts, press, partnerships). New `TractionSignal` schema, model populates them with verbatim spans, source-URL guard rejects fabricated citations. W26 dogfood: **73 of 105 high-confidence companies surfaced 212 traction signals across 8 kinds**.
- **3-POV slide** in the deck for parity with the memo; named figures live in a single dict so memo and deck can never disagree.
- **README hero screenshot** of the dashboard (auto-generated via Playwright at PR time).
- **Bug fix**: dashboard chart-options JSON was being HTML-escaped before injection into a `<script type="application/json">` block, which broke `JSON.parse` on the client. Real charts in the v0.2.0 example HTML now render in browsers as well as in Playwright.

## [0.2.0] — 2026-05-01

Phase 2 release. Adds the depth=1 website crawler that lifts OSS-posture
classification from 55% unknown to 21%, replaces the dashboard's static CSS
bars with Apache ECharts, and ships the VC-style `.pptx` deck and narrative
`.docx` memo with a second anti-hallucination layer that scans aggregate
prose for forbidden hedge phrases and audits every number against the same
dataframe the dashboard cites.

### Added
- **PR #11 — depth=1 website crawl (B007 resolved)**: new `src/ycai/crawler.py` module. Polite, robots-aware, max 5 pages per company, 30 KB per page, 4-second timeout. Pages ranked by signal-path priority (`/pricing`, `/security`, `/about`, `/docs`, `/open-source`, …). HTML stripped and PII-sanitized before any LLM call. Crawled URLs are also accepted by the source-URL guard so the LLM can cite specific pages as evidence. New `--no-crawl` flag opts out.
- W26 with crawler enabled: **OSS posture `unknown` rate dropped 55% → 21%** (target was <30%). Tech-stack identified mentions: 14 → 41. Vision capability: 17 → 26. Multimodal: 17 → 22.
- 13 new crawler tests (116 total), all network-free via `httpx.MockTransport`. Robots-disallow path-level enforcement, content-type filtering (PDF/JSON skipped), max-pages cap, dedup, fragment stripping, host-restriction (no off-site fetches), PII redaction round-trip.

### Changed
- **PR #12 — Apache ECharts replaces static CSS bars in the dashboard.** Heatmap is now a real 2D heatmap with hover tooltips. Pie charts (confidence, OSS posture) render with proper labeling and click-to-isolate. Bar charts (industry, tech stack, YC tags, regions) get axis pointers and value tooltips. Loaded from CDN with SRI-pinned integrity hash; falls back to a `<noscript>` table if JS is disabled or the CDN is blocked. Each canvas carries `role="img"` + descriptive `aria-label`. All chart options ship as pure JSON in a `<script type="application/json">` block — no JS function strings, no client-side `eval`. 7 chart canvases, 121 tests passing.

### Phase 2 — reports
- **PR #14 — VC-style `.pptx` deck with anti-hallucination Layer 2.** New `src/ycai/analytics.py` is the single source of chart math, consumed by both the dashboard (ECharts JSON) and the deck (matplotlib PNG). New `src/ycai/reports/ppt.py` builds a 16-slide deck (cream/orange palette, sans/serif typography). Each chart is a matplotlib PNG anchored to the same Counter the dashboard used. `ycai report <run-dir>` produces `deck.pptx` from existing artifacts at zero LLM cost. New `src/ycai/reports/anti_hallucination.py`: forbidden-phrase scan + numerical-drift check + date-pattern stripping. Two prose streams audited separately — aggregate commentary gets full drift check, per-company taglines/rationales get forbidden-phrase only (Layer 1 already gated their source URLs). 24 new tests (145 total).
- **PR #15 — narrative `.docx` memo.** New `src/ycai/reports/docx.py` builds a 9-section narrative memo per USER.md document-format discipline: title, headline, coverage methodology, the agentic batch (capability heatmap), industry distribution, tech stack + OSS posture, six company spotlights, unanswered questions, reproducibility. Same `analytics.py` math as the deck, same Layer 2 audit pre-write. Date-pattern stripping extended to YC-batch labels ("Winter 2026") and bare 4-digit years. `ycai report <run-dir>` now produces both `deck.pptx` and `report.docx`; `--deck-only` / `--memo-only` to constrain. 4 new tests (149 total).

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

[Unreleased]: https://github.com/RyanAlberts/yc-ai-pulse/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/RyanAlberts/yc-ai-pulse/releases/tag/v0.2.0
[0.1.0]: https://github.com/RyanAlberts/yc-ai-pulse/releases/tag/v0.1.0
