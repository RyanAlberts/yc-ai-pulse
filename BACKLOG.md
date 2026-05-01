# BACKLOG

Working list of improvements noticed during execution but deferred to a later phase.

Format:

```
- [B###] short description — surfaced in: <PR/phase> — proposed: <phase>
```

Promoted to GitHub issues when an item survives more than one PR. ADRs for non-trivial decisions live in `docs/decisions/`.

---

## Open

- [B001] yc-oss/api is now sole source for batch listing — the previously planned `ycombinator.com/companies?batch=...` fallback is disallowed by robots.txt. PR #1 must implement a hard-fail path when yc-oss is unreachable, plus an upstream-staleness CI cron. — surfaced in: phase 0 verification — proposed: PR #1
- [B002] Confirm Cloudflare or upstream caching on `yc-oss.github.io/api/*` for our use case (rate limit headroom on full-batch sweeps). — surfaced in: phase 0 — proposed: PR #1
- [B003] CI annotations report Node 20 actions deprecated (forced to Node 24 from 2026-06-02). Refresh `actions/checkout`, `actions/setup-python`, `gitleaks/gitleaks-action` to Node-24-compatible majors before that date. — surfaced in: phase 0 CI run — proposed: ad-hoc PR before 2026-06-02
- [B004] Tune `MIN_DESCRIPTION_CHARS` (currently 80). The W26 probe surfaced one borderline drop (`moda`, 57 chars). A small calibration study against borderline rows would let us pick a defensible threshold. — surfaced in: W26 quality probe — proposed: PR #2
- [B005] Name the missing-from-upstream companies, not just count them. Compare yc-oss slugs to a slug list discovered from `/companies/<slug>` profile pages so the dropped register includes "Acme (in YC W26 but not in yc-oss/api)". — surfaced in: W26 quality probe — proposed: PR #2 or #3
- [B006] Track schema-validation failure rate during enrichment as a tracked metric. The W26 smoke run had 1/5 (20%) parse failures (`velum-labs` — likely rationale exceeded the 400 char limit). Measure this across the full batch and tune prompt or schema if rate exceeds ~5%. — surfaced in: PR #2 smoke — proposed: PR #3
- [B007] Tech-stack and OSS-posture nearly always come back as `unknown` because the model only sees the YC `long_description`, not the company website. Adding a depth=1 website crawl before the LLM call would let the model identify e.g. "this product is closed-source SaaS" or "uses OpenAI" — significantly improving Tier A signal density. Cost: ~5-10 KB extra context per company. — surfaced in: PR #2 smoke. Confirmed in PR #3 full run: 45 of 83 high-confidence rows have OSS posture `unknown`, 52 have tech_stack `unknown`. — proposed: PR after v0.1
- [B008] Schema-validation failure rate on the full W26 enrichment was **23%** (29 of 124). The lenient parser added in PR #3 only relaxed `industry_secondary`. Most remaining failures likely come from the model emitting `ai_capability` or `tech_stack` values outside our closed enums. Either extend the lenient parser to those fields, capture a sample of raw failed responses to audit, or introduce `tool_use`-style schema enforcement on the API backend so the model is constrained at decode time. — surfaced in: PR #3 full run — proposed: PR #4 (CLI polish)

## Done

_(empty — moves here when the backing PR merges)_
