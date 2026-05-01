## What

<!-- One sentence on what changes for the user. -->

## Why

<!-- The motivation. Link the issue if there is one. -->

## How

<!-- Brief implementation notes. Diagrams encouraged for non-trivial changes. -->

## Acceptance

- [ ] `make validate-p0` (or current phase) green locally
- [ ] `make publish-check` green
- [ ] Pre-commit hooks all pass
- [ ] No new lines in `BACKLOG.md` deferred without a reason
- [ ] If this PR touches the LLM path: anti-hallucination invariants preserved (see [CONTRIBUTING.md](../CONTRIBUTING.md))
- [ ] If this PR touches the extension: Playwright suite passes

## Test plan

<!-- How did you verify this? Include any manual smoke steps. -->
