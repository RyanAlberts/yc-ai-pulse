# Contributing

Thanks for considering a contribution. A few ground rules keep the project healthy.

## Setup

```bash
git clone https://github.com/RyanAlberts/yc-ai-pulse.git
cd yc-ai-pulse
make install        # installs pre-commit + dev deps
pip install -e ".[dev]"
```

## Before opening a PR

```bash
make validate-p0    # lint, typecheck, test, secret-scan
make publish-check  # hygiene gate before pushing
```

CI re-runs both. PRs cannot merge if either fails.

## Code style

- Ruff handles formatting and most lint rules. Run `ruff format`.
- `mypy --strict` is the floor for new code.
- Comments only when the *why* is non-obvious. Don't restate what the code says.
- Keep PRs small. The plan calls for 3-5 PRs per phase, each independently mergeable.

## Anti-hallucination contract (non-negotiable for any PR that touches the LLM path)

If your PR adds or modifies code under `src/ycai/researcher.py` or `src/ycai/reports/`, you must keep the following invariants:

1. All Sonnet calls return validated pydantic models. No free-text JSON parsing.
2. Numbers come from the DataFrame, never from the LLM.
3. Every analysis row has ≥1 source URL.
4. The forbidden-phrase scanner stays green.
5. Every quote in a report has a re-verifiable source span.
6. The link-verifier hard gate is not bypassed.

If a PR weakens any of these, the reviewer will block it.

## PII / secrets

Never commit:
- Real API keys (`sk-ant-...`, `ghp_...`, etc.)
- Real founder emails, phone numbers, or addresses
- Anything in `runs/` (gitignored — but double-check)

`make publish-check` and CI both enforce this. If you bypass it, the PR will not merge.

## Filing an issue

Use the issue templates. Tag with the right phase milestone (`v0.1`, `v0.2`, `v1.0`).

Small bugs and tangential improvements should also be added to [`BACKLOG.md`](BACKLOG.md) so they don't get lost between sessions.
