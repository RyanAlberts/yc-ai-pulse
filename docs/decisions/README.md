# Architecture Decision Records (ADRs)

Each non-trivial decision gets one short ADR. Format:

- One file per decision: `NNNN-short-slug.md`
- Status: Proposed / Accepted / Superseded by ADR-XXXX / Deprecated
- Sections: Context, Decision, Consequences (positive + negative), Alternatives rejected, Verification.

The point: future-me reads three paragraphs and understands *why*, not just *what*.

## Index

- [0001 — Use yc-oss/api as the primary YC data source](0001-yc-data-source.md)
- [0002 — Chrome extension talks to a local FastAPI, not Native Messaging](0002-localhost-vs-native-messaging.md)
