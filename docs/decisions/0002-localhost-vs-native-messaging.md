# ADR 0002 — Chrome extension talks to a local FastAPI, not Native Messaging

**Date:** 2026-05-01
**Status:** Accepted

## Context

The Chrome MV3 extension needs to trigger a long-running Python pipeline. Two approaches:

1. **Native Messaging** — `chrome.runtime.connectNative()` + a per-OS host manifest. More secure (no open port) but requires a per-platform install step.
2. **Localhost fetch** — extension calls `http://localhost:8787` against a FastAPI process the user starts. Simpler, but exposes a port.

## Decision

Use **localhost FastAPI** wrapped in a friendly daemon command (`ycai daemon start`). Bind to `127.0.0.1` only. CORS limited to `chrome-extension://*`. No native messaging in v1.

## Consequences

**Positive**
- One-line install path: `pipx install yc-ai-pulse && ycai daemon start`. Friction-free for OSS adoption.
- Cross-platform with zero per-OS install scripts.
- Easy to debug — the extension talks to a normal HTTP endpoint visible in DevTools.

**Negative**
- A port is open on the user's machine while the daemon runs. Mitigated by `127.0.0.1`-only bind, no auth bypass exposure.
- Other local apps (or other Chrome extensions on the same machine) could in theory reach the port. Mitigated by:
  1. Bind to `127.0.0.1` only — not reachable from network.
  2. Origin allowlist for `chrome-extension://<our-extension-id>` only.
  3. Endpoint requires the extension to send an installation token issued at first daemon start (stored in `~/.ycai/`).

## Alternatives rejected

- **Native Messaging** — keeps the door closed but adds enough install friction to hurt OSS adoption. Reconsider for a v2 hardened build.
- **Hosted API** — eliminates local install but adds ongoing inference cost the maintainer eats. Conflicts with the "user uses their own subscription" model.

## Verification

Phase 3 acceptance gate includes:
- Confirm bind is `127.0.0.1` only via `lsof -i :8787` smoke test.
- Confirm CORS rejects requests from `https://example.com` in CI.
- Confirm token check prevents an unauthorized `curl localhost:8787/run`.
