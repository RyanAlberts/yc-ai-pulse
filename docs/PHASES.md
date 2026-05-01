# Phases and acceptance gates

Each phase has a `make validate-pN` target. Phase N+1 work cannot merge until Phase N's gate is green.

## Phase 0 — Bootstrap (current)

- MIT license, secrets hygiene, pre-commit + gitleaks + detect-secrets, CI green on empty scaffold.
- `make publish-check` green.
- Repo live on GitHub with first commit on `main`.

## Phase 1 — CLI + dashboard

- `ycai run --depth quick` finishes in <10 min on subscription.
- 5-chart `dashboard.html` with row-level drill-downs.
- Anti-hallucination Layer 1: schema-enforced output, two-pass cross-check, sources required, hallucination-trap fixture green.
- Link-verify hard gate.
- ≥85% coverage on `classifier.py`, `verifier.py`, `sanitizer.py`.

## Phase 2 — Reports

- `deck.pptx` + `report.docx` from a single run.
- Anti-hallucination Layer 2: chart-CSV drift check, forbidden-phrase scanner, evidence-anchor enforcement, quote re-verification.
- All cited URLs pass link-verify at build time.

## Phase 3 — Chrome extension

- MV3 extension, Playwright E2E suite in CI.
- Two flows: batch summary, single-company deep-dive.
- Daemon mode with friendly start/stop/status.
- README quickstart works on a clean macOS Sequoia and Ubuntu 24.04 VM.

See [`../create-an-open-source-feature-effervescent-catmull.md`](../../create-an-open-source-feature-effervescent-catmull.md) for the full plan.
