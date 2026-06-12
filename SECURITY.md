# Security policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in `yc-ai-pulse`, please **do not** open a public GitHub issue. Instead, use **GitHub’s private vulnerability reporting** ("Security" → "Report a vulnerability" on this repository) with details.

Reasonable response within 7 days. Acknowledgement within 30 days where possible.

## Scope

In-scope:

- The Python package (`yc-ai-pulse` on PyPI).
- The Chrome extension distributed via GitHub releases.
- The local FastAPI daemon that the extension talks to.

Out-of-scope:

- Vulnerabilities in third-party dependencies (please report upstream).
- Issues in `yc-oss/api` (please report at the [yc-oss repo](https://github.com/yc-oss/api)).
- Issues in Chromium itself.

## Security posture

`yc-ai-pulse` is a local-only tool. The daemon binds to `127.0.0.1`, the extension talks only to localhost, and no telemetry is collected. See [docs/decisions/0002-localhost-vs-native-messaging.md](docs/decisions/0002-localhost-vs-native-messaging.md) for the threat model.
