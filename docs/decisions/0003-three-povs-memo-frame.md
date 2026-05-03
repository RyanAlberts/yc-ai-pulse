# ADR 0003 — Three-POV introduction frame in the memo

**Date:** 2026-05-03
**Status:** Accepted

## Context

The memo's purpose is to inform capital-allocation decisions, not to certify
that a YC batch is "good" or "bad". An LLM-generated narrative without a
named editorial frame defaults to bland techno-optimism — agents are real,
software is eating the world, etc. — which is true but useless to a reader
making allocation calls in 2026 against debt cycles, AI-productivity
skepticism, and sectoral concentration.

The reader needs a frame to argue *with*, not absorb. The cheapest,
sharpest frame is to name three public voices whose views materially
disagree and let the data set sit between them.

## Decision

Every memo opens with a single-paragraph "introduction" that pits three
named public voices against each other on what the batch findings imply
for capital allocation:

- **Marc Andreessen** (a16z) — techno-optimist; AI is the dominant
  industrial transition of our generation; concentrate capital in the
  winners; regulation is the existential threat. Source: ["The
  Techno-Optimist Manifesto"](https://a16z.com/the-techno-optimist-manifesto/), 2023.
- **Ray Dalio** (Bridgewater) — paradigm-shift macro; AI is real but is
  one factor among many; debt cycles, monetary regimes, and geopolitical
  realignment dominate; diversify. Source: Dalio's "Principles for Dealing
  with the Changing World Order" + ongoing LinkedIn essays.
- **Daron Acemoglu** (2024 Nobel laureate in Economics, MIT) — AI's
  productivity claims are likely overstated. His [2024 NBER working paper
  10.3386/w32487](https://www.nber.org/papers/w32487) estimates total-factor productivity gains from AI
  over the next decade at <0.66%, with the largest distributional risk
  being labor-displacement-without-reabsorption. Investment frame: weight
  redistributive risk and policy response, not just productivity TAM.

The memo does **not** pick a winner. The job of the introduction is to
force the reader to make a judgment, not to absorb a pre-cooked answer.

## Consequences

**Positive**
- The memo becomes useful *as input to a real capital decision* rather
  than as a tour of YC's classification taxonomy.
- The three names provide an editorial spine that survives a YC batch
  whose findings could be cherry-picked to support any one of them.
- Anti-hallucination Layer 2's forbidden-phrase scan is unaffected — the
  three figures are named real public people whose published views are
  being summarized, not anonymous "industry insiders".

**Negative**
- We become responsible for representing each figure's view fairly.
  Mitigated by linking to their actual published statements, paraphrasing
  rather than fabricating quotes, and updating the doc when the figures'
  positions evolve materially.
- The frame is opinionated. A maintainer who wants a "neutral" memo
  should fork or override.

## Alternatives rejected

- **No frame** — leaves the memo prose to defaults, i.e. techno-optimist
  haze. Tested in PR #15; the result reads like a press release.
- **One frame** (e.g. just Acemoglu) — replaces one bias with another.
  Three voices disagreeing forces the reader's own synthesis.
- **Five frames** — three is the minimum number of distinct positions
  that span the optimist / hedger / skeptic axis. Five over-flattens.

## Verification

- `tests/test_docx.py::test_memo_introduction_includes_three_named_figures`
  asserts the three names appear in the introduction paragraph.
- The named-figures list is **not** in `FORBIDDEN_PHRASES`; appearance
  passes Layer 2.
- `docs/MEMO_STRUCTURE.md` is the maintainer-facing instruction; this ADR
  is the rationale.

## Updating the figures

If one of the three names becomes inappropriate (e.g. retracted views,
defamation risk, factual error), the change requires:
1. A successor ADR (this one moves to "Superseded").
2. A test update to the new names.
3. A README + memo regeneration.

The list is intentionally short to make this churn cheap.
