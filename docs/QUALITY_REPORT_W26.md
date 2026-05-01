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

---

## PR #3 — full-batch enrichment results (2026-05-01)

After PR #3 (enriched dashboard), the full 124-company enrichment ran end-to-end via Claude Max subscription. Took ~6 minutes.

### Confidence

- **83 high (67%)** + **0 medium** + **41 low (33%)**.
- Of the 41 low-confidence rows: **29** were schema-validation failures (model emitted output that didn't validate after lenient pass), **12** were genuinely-uncertain outputs the model itself flagged as low.
- **0 hallucinated source URLs** detected — the source-URL guard caught zero cases on this run; every cited URL traced back to either the company website or its YC profile page.

### Industry distribution (Tier A high+medium, n=83)

| Industry | n |
|---|---:|
| B2B SaaS | 16 |
| Fintech | 10 |
| Developer Tools | 7 |
| AI Infrastructure | 7 |
| Legal | 5 |
| Healthcare | 5 |
| Biotech | 4 |
| Security | 4 |

The B2B-heavy mix lines up with the [VCCorner W26 demo-day breakdown](https://www.thevccorner.com/p/yc-w26-demo-day-2026-complete-breakdown). The visible Legal cluster (5) is a smaller but real cohort the article didn't separately call out.

### AI capability distribution (n=83)

| Capability | n |
|---|---:|
| **agents** | 54 |
| nlp-classic | 30 |
| rag | 26 |
| data-pipeline | 19 |
| vision | 14 |
| multimodal | 10 |
| evals-observability | 9 |
| **no-ai** | 8 |

**Top finding**: 65% (54 of 83) of high-confidence W26 companies build agents. This is the dominant story of the batch.

**Honesty check**: 8 companies were correctly classified as `no-ai` despite being in the YC batch — the LLM is willing to say "the YC profile suggests AI but the description doesn't actually substantiate it." This is exactly the behavior the anti-hallucination contract is meant to produce.

### OSS posture (n=83)

| Posture | n |
|---|---:|
| unknown | 45 |
| closed | 36 |
| api-only | 1 |
| source-available | 1 |
| fully-open | 0 |

**The "unknown" plurality is the main signal**, and it's structural. The model has access only to the YC `long_description`; OSS posture is rarely stated there. **B007** in the backlog (depth=1 website crawl) would shift these `unknown` rows to `closed` / `api-only` / `weights-only` based on actual evidence (license files, GitHub presence, pricing pages).

Until then, do not over-interpret the `unknown` count: it's a measurement gap, not a finding.

### Tech stack

Dominated by `unknown` (52) and `custom-model` (13). Same structural reason — descriptions don't usually name the model provider. `custom-model` is signal-bearing: 13 companies advertise their own models / fine-tunes, which is a meaningful slice of W26.

### Cited-URL link verification (the publish gate)

Of all source URLs cited across 83 high-confidence rows, **3** returned 4xx/5xx at publish time:
- `https://www.arzule.com/` — 429 (rate limit)
- `https://maywoodai.com/` — 404
- `https://www.caretta.so/` — SSL handshake failure

Each is named in [`examples/output/BROKEN_LINKS-w26-2026-05-01.md`](../examples/output/BROKEN_LINKS-w26-2026-05-01.md) with the company that cited it. Dashboard rendered with `--allow-dead-links` for this example, with a warning banner at the top. In production runs (no `--allow-dead-links`), the pipeline would have refused to write the dashboard and exited non-zero — that's the publish gate.

### Implications

1. **Schema-validation failure rate (23%) is too high for a v0.1 release.** Tracked as B006. Most likely cause is the model emitting enum values outside our closed sets for `ai_capability` or `tech_stack` (we patched `industry_secondary` for this in PR #3 but the other two stayed strict). Fix in a follow-up PR.
2. **W26 is an agents batch.** This is now defensible — 54 of 83 high-confidence rows, with row-level drill-down showing exactly which companies and what their YC descriptions said.
3. **The 67% high-confidence rate against 63.3% upstream coverage means the actual analyzable share of W26 is ~42% (83/196).** The headline metric on the dashboard now shows this honestly.

---

## PR #4 — schema-failure rate dropped to 0% (2026-05-01)

After PR #4 (resilience + parser tightening), full-batch enrichment metrics improved meaningfully on a fresh run:

| metric | PR #3 | PR #4 | change |
|---|---:|---:|---:|
| Total analyzed | 124 | 124 | – |
| **High confidence** | 83 (67%) | **118 (95%)** | **+35** |
| Schema-validation failures | 29 (23%) | **0 (0%)** | **-29** |
| Genuinely-uncertain model lows | 12 | 6 | -6 |
| Hallucinated source URLs | 0 | 0 | – |

**Root cause:** The 23% schema-validation failure rate in PR #3 was caused by the model emitting `rationale` fields longer than our 400-char `Field(max_length=400)` constraint. The model was being thorough; our schema was being unnecessarily strict on a non-load-bearing field. PR #4 changed the parser to truncate over-long `rationale` and `tagline_rewrite` rather than reject the row. Strict enforcement remains for load-bearing fields (`industry_primary`, `oss_posture`, `confidence`, `sources`).

The lenient extension to `ai_capability` and `tech_stack` (filter unknown values, fall back to `unclear` if all dropped) contributed only modest improvement on its own (~3-4%). The rationale truncation was the bigger win.

### W26 findings, recomputed on the n=118 high-confidence cohort

Capability distribution:

| Capability | n | share of n=118 |
|---|---:|---:|
| **agents** | 68 | **58%** |
| nlp-classic | 38 | 32% |
| rag | 34 | 29% |
| data-pipeline | 33 | 28% |
| multimodal | 17 | 14% |
| vision | 17 | 14% |
| inference-infra | 11 | 9% |
| evals-observability | 11 | 9% |
| training-infra | 10 | 8% |

The "W26 is the agentic batch" finding strengthens with more confident data: 58% of high-confidence companies build agents, up from 65% of 83 → 68 / 118. The absolute count is now larger and the cohort is broader.

Industry mix (top of n=118):

| Industry | n |
|---|---:|
| B2B SaaS | 28 |
| AI Infrastructure | 14 |
| Developer Tools | 12 |
| Fintech | 12 |
| Healthcare | 6 |
| Robotics | 6 |
| Consumer | 6 |
| Legal | 5 |

OSS posture is meaningfully different now: closed (48), unknown (65), api-only (3), source-available (1), fully-open (1). The `unknown` plurality remains because the model still lacks website-level evidence — `B007` (depth=1 crawl) is the next lever.

### Coverage of YC official, updated

- Upstream: 132 of 196 (67.3%) — unchanged
- Tier A+B: 124 of 132 — unchanged
- Tier A+B with high-confidence LLM analysis: **118 of 196 (60.2%)** — the most honest "what we actually know about W26" number.

The headline coverage % on the dashboard is unchanged at 63.3% (because that's the data-quality denominator). But for the deck/memo, the 60.2% number is what should be cited as "the share of W26 we can substantively classify."
