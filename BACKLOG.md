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

## Done

_(empty — moves here when the backing PR merges)_
