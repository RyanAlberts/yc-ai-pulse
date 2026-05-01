# ADR 0001 — Use yc-oss/api as the primary YC data source

**Date:** 2026-05-01
**Status:** Accepted

## Context

The pipeline needs a reliable list of every company in the most recent YC batch (currently W26, 196 companies). Three sources exist:

1. **Direct scrape** of `ycombinator.com/companies` — works but fragile (Next.js hydration, rate limits, robots.txt unconfirmed).
2. **[yc-oss/api](https://github.com/yc-oss/api)** — community-maintained JSON refreshed daily, indexed by batch.
3. **Apify-hosted scrapers** — paid, requires a token, adds an external dependency on a third-party platform.

## Decision

Use **yc-oss/api as the primary source**, with a direct-scrape fallback only when yc-oss is stale (>24h since last update).

## Consequences

**Positive**
- Deterministic, cacheable JSON. Tests can pin to a frozen fixture.
- No rate-limit risk, no scraping etiquette concerns.
- The community already does the maintenance work.
- Stable schema across batches → less per-batch breakage risk.

**Negative**
- We inherit the `yc-oss/api` schema. If they rename a field, we patch.
- Daily refresh latency means brand-new companies (added mid-batch) may be missing for up to 24h.
- We're a leaf consumer of someone else's volunteer effort. Mitigate by linking back from the README and contributing fixes upstream when we find issues.

## Alternatives rejected

- **Apify** — adds paid dependency, conflicts with the OSS-first goal.
- **Direct scrape only** — works but more fragile. Kept as fallback.

## Verification

PR #1 will add a small `scripts/check_yc_oss.py` that runs in CI and warns (does not fail) if `yc-oss/api`'s latest-batch JSON hasn't been updated in >48h, so we notice upstream staleness early.
