# yc-ai-pulse

> Open-source YC batch analyzer. One command produces an evidence-grounded VC-style research report and dashboard for the most recent Y Combinator batch.

[![CI](https://github.com/RyanAlberts/yc-ai-pulse/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanAlberts/yc-ai-pulse/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## What it does

`yc-ai-pulse` answers: *what does the most recent YC batch tell us about the state of AI?*

For every company in the latest batch, it classifies:

- **Industry** — primary + secondary
- **AI capability** — code-gen, agents, RAG, voice, vision, robotics, ...
- **Tech stack** — model providers, frameworks, infra
- **OSS posture** — fully-open, weights-only, api-only, closed

Then it produces three artifacts, all grounded in a single CSV — no hallucinated numbers, every quote traceable, every link verified before publish:

1. **Interactive HTML dashboard** with drill-down to source rows
2. **VC-style PowerPoint deck** (a16z aesthetic) — `.pptx`
3. **Narrative memo** — `.docx`

A Chrome extension wraps two flows: *(a)* analyze the whole batch, *(b)* deep-dive a single company.

## Status

| Phase | Surface | Status |
|---|---|---|
| 0 | Repo bootstrap, secrets hygiene, CI | ✅ shipped |
| 1 | CLI + dashboard with anti-hallucination Layer 1 | ✅ **v0.1.0** |
| 2 | Depth=1 crawler + ECharts dashboard + `.pptx` deck + `.docx` memo | ✅ shipped |
| 3 | Chrome extension | ⬜ planned |

See [CHANGELOG.md](CHANGELOG.md) for what 0.1.0 includes, [BACKLOG.md](BACKLOG.md) for the working backlog, and `docs/decisions/` for architecture decisions.

## Quickstart (v0.1.0)

```bash
pipx install yc-ai-pulse                                     # or: uv tool install yc-ai-pulse

# Coverage probe only (no LLM cost) — fetches the latest batch and shows
# what's analyzable. Headline: % of YC batch covered, with the dropped
# register naming every excluded company.
ycai run-coverage --batch winter-2026 --yc-official-count 196

# Full enrichment via your Claude Max subscription (~6 min on W26, ~free).
# 95% high-confidence rate. Renders the dashboard with capability heatmap,
# tech-stack and OSS-posture breakdowns. Refuses to write the dashboard
# if any cited URL is dead.
ycai run-coverage --batch winter-2026 --yc-official-count 196 --enrich

# Pay-per-token instead of subscription:
export ANTHROPIC_API_KEY=sk-ant-...
ycai run-coverage --batch winter-2026 --yc-official-count 196 --enrich

# Resume an interrupted run (quota wall, crash, network blip):
ycai resume runs/2026-05-01-XXXXXX

# Re-render the dashboard from existing artifacts at zero LLM cost
# (useful when the dashboard layout changes):
ycai dashboard runs/2026-05-01-XXXXXX
```

A real run on YC W26 is checked in as a working example: see [`examples/output/dashboard-w26-pr4-2026-05-01.html`](examples/output/dashboard-w26-pr4-2026-05-01.html). The full quality writeup is in [`docs/QUALITY_REPORT_W26.md`](docs/QUALITY_REPORT_W26.md).

## Anti-hallucination guarantees

This tool is built around the rule that *numbers come from `pandas`, never from the LLM.* Concretely:

- **Schema-enforced LLM output** — pydantic models reject any row without ≥1 source URL.
- **Two-pass cross-check** on uncertain classifications; disagreements drop out of charts.
- **Numerical drift check** — any number in generated prose that's not in the underlying CSV fails the build.
- **Forbidden-phrase scanner** — "studies show", "experts say", and similar lazy hedges fail the build.
- **Quote-span verification** — every quote re-fetched from source at report time, byte-compared.
- **Link-verify hard gate** — no artifact ships if any cited link returns 4xx/5xx.

Each chart in the dashboard exposes the source rows that produced it. If the data isn't there, the chart doesn't render.

## Privacy

`yc-ai-pulse` is local-only. No telemetry, no remote config, no data leaves your machine except for the LLM calls (to Anthropic) and the public-page fetches you'd make in a browser.

The pipeline collects only data already public on yc.com. A `sanitizer.py` strips emails, phone numbers, and addresses defensively before any data hits disk *or* the LLM.

## License

MIT — see [LICENSE](LICENSE).
