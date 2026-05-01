# W26 quality probe — 2026-05-01

First end-to-end run of the Phase 1 quality probe. No LLM calls; this is the
data-quality floor against which classification + report generation will run
in subsequent PRs.

## Headline

**63.3% of YC W26 analyzed** — 124 of 196 companies pass the data-quality bar.

## Coverage breakdown

| Source | Count | Notes |
|---|---:|---|
| YC W26 official (Demo Day, 2026-03-24) | 196 | Per the [VC Corner W26 breakdown](https://www.thevccorner.com/p/yc-w26-demo-day-2026-complete-breakdown). |
| yc-oss/api fixture (last refreshed 2026-02-08) | 132 | 64 companies missing — upstream is stale by ~3 months. |
| Tier A (full classification) | 120 | All required fields + website returned 2xx/3xx. |
| Tier B (partial — website unreachable) | 4 | Required fields present; website 4xx/5xx. Kept in charts with a flag. |
| Tier C (excluded) | 8 | Acknowledged in the dropped register below. |
| **Analyzable (A + B)** | **124** | Feeds every chart in the dashboard. |

**Coverage of upstream:** 93.9% (124 / 132).
**Coverage of YC official:** 63.3% (124 / 196). ← **headline metric**

## Why the gap

### 1. Upstream staleness (the bigger problem — 64 companies)

`yc-oss/api`'s `meta.json` reports `last_updated: 2026-02-08T01:49:11Z`. W26 Demo Day was **2026-03-24**, so the upstream was last refreshed ~6 weeks before the batch closed. The Demo Day–era cohort (~64 companies) is missing from the feed entirely.

This is not a bug in `yc-ai-pulse` — `yc-oss/api` is community-maintained. Mitigations:

1. **Already in place:** the dashboard surfaces this gap upfront ("Upstream gap" alert banner).
2. **B003 (open in BACKLOG):** add a CI cron that warns if the upstream is >48h stale. The W26 case would have tripped it ~3 months ago.
3. **Future:** consider a direct YC profile-page enrichment (allowed under robots.txt for `/companies/<slug>`) for slug lists discovered from elsewhere. Not in v0.1 scope.

### 2. Per-company drops (8 companies)

Eight companies in the upstream feed were excluded from charts because they're missing fields the analysis layer requires. They are listed by name:

| Slug | Name | Reason |
|---|---|---|
| `protent` | Protent | `long_description` empty |
| `byteport` | Byteport | `long_description` empty |
| `zerosettle` | ZeroSettle | `long_description` empty |
| `traverse` | Traverse | `long_description` empty |
| `grade` | Grade | `long_description` empty |
| `zymbly` | Zymbly | `long_description` empty |
| `moda` | Moda | `long_description` 57 chars (below 80-char threshold) |
| `condor-energy` | Condor Energy | `website` field empty |

Auditable threshold: `MIN_DESCRIPTION_CHARS = 80` ([src/ycai/coverage.py](../src/ycai/coverage.py)). Lowering it to 50 would bring `moda` back; raising it to 120 would drop ~6 more borderline rows. The current threshold balances inclusion with the requirement that classification be evidence-backed.

### 3. Dead websites (4 companies — kept as Tier B)

Tier B keeps these companies in the analysis but flags them in the dashboard:

- `maywood` — Maywood
- `caretta` — Caretta
- `arzule` — Arzule
- `servo7` — Servo7

These had 4xx/5xx responses at probe time. Could be transient. The verifier reruns at report build time (PR #3 acceptance gate).

## What we already know about the analyzable 124

Industry distribution (from the YC-supplied `industry` field, no LLM yet):

| Industry | Count |
|---|---:|
| B2B | 80 |
| Industrials | 18 |
| Healthcare | 9 |
| Fintech | 8 |
| Consumer | 6 |
| Real Estate and Construction | 3 |

The B2B-heavy distribution lines up with the [thevccorner.com breakdown](https://www.thevccorner.com/p/yc-w26-demo-day-2026-complete-breakdown) (64% B2B for W26). Internal consistency check passes.

## Verifier results

- `ok` (2xx/3xx): **127** websites
- `dead` (4xx/5xx): **4** websites
- `slow` (>5s): 0
- `redirect` (>3 hops): 0
- `error` (network): 0

## Reproducing this run

```bash
PYTHONPATH=src python3 -m ycai.cli run-coverage \
  --batch winter-2026 \
  --yc-official-count 196
```

Output: `runs/2026-05-01-185520/{dashboard.html, coverage.json, companies.csv}`.

## Implications for downstream PRs

- **PR #2 (researcher + classifier):** must consume `coverage.json` directly so its denominator agrees with the dashboard. The LLM never sees Tier C rows.
- **PR #3 (deck/memo):** the methodology slide must show the same 63.3% headline, same upstream-gap callout, same dropped-register table. CI should fail if the deck cites a different denominator.
- **PR #5 (release):** consider adding a "data freshness" indicator to the README badge so users know if the latest cached run is from a stale upstream.

## Open follow-ups (added to BACKLOG)

- [B004] Tune `MIN_DESCRIPTION_CHARS`. 80 is a guess; a small calibration study against the 8 borderline companies would let us pick a defensible value.
- [B005] Add a "what's missing" section to the dashboard that compares yc-oss slugs to a slug list discovered from the YC `/companies/<slug>` profile pages, so we can name the 64 missing W26 companies, not just count them.
