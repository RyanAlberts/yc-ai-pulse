# Memo structure (the codified instruction)

This file is load-bearing. Anyone editing `src/ycai/reports/docx.py` should
keep the structure below intact unless the README, this file, ADR 0003, and
the memo all change together.

The memo (`report.docx`) is a narrative document, 3-6 pages, with embedded
chart PNGs and verbatim citations. Per [USER.md document-format
discipline](#) it is the canonical *strategic* surface. The deck mirrors it
visually but the memo is the load-bearing prose.

## Required sections, in order

1. **Title + dateline** — batch label, generation timestamp, repo URL.
2. **Executive summary** — 3-4 sentences. Headline coverage %, headline
   capability finding (e.g. "X of Y companies build agents"), one Nobel
   laureate's framing of what that means for capital allocation.
3. **Introduction (three POVs)** — single paragraph that pits three named
   public voices against each other on what to do with this batch:
     - **Marc Andreessen** (a16z) — techno-optimist concentrate-and-bet
     - **Ray Dalio** (Bridgewater) — diversify, weight macro cycles
     - **Daron Acemoglu** (2024 Nobel laureate, MIT) — productivity claims
       are inflated; weight labor-displacement and redistribution risk
   The paragraph should not pick a winner. It frames the batch findings as
   an empirical input that all three would interpret differently.
4. **Coverage and methodology** — Tier A/B/C breakdown, Layer 1+2 disclosure.
5. **The agentic batch** — capability heatmap + analysis paragraph.
6. **Industry distribution** — top-level industry chart.
7. **Inside B2B SaaS** — one-layer-deeper breakdown of the largest industry
   bucket using YC's `subindustry` field. Pure passthrough math (not
   LLM-derived) so the breakdown can't drift.
8. **Tech stack and OSS posture** — chart of *known* tech-stack mentions
   only; the unknown count is rendered as a footnote/asterisk under the
   chart, not as a chart bar.
9. **Traction signals** — companies that advertise verifiable traction
   (GitHub stars, named customers, funding rounds, revenue, user counts,
   press, partnerships). One section per signal kind, capped at 5
   companies per kind for legibility, with verbatim detail and a citable
   source URL. Companies without any traction signal are not listed.
10. **Six company spotlights** — diverse-capability + non-B2B-SaaS picks.
    Each spotlight includes its traction signals as bullets when present.
11. **What we still cannot answer** — three open questions framed against
    the introduction's three POVs.
12. **Reproduce this memo** — install + run instructions.

## Why this structure

- **Executive summary first** because most readers don't read past page one.
  The Nobel POV in section 2 is what makes the memo *useful as input to a
  capital allocation decision* — without it, this is just classification.
- **Three-POV introduction** because no single voice on AI is dispositive
  in 2026. Pitting Andreessen, Dalio, and Acemoglu against each other forces
  the reader to make a judgment rather than absorb a pre-cooked answer.
- **Sub-industry breakdown** because "B2B SaaS" is the laziest taxonomy
  bucket in venture and tells you nothing useful. One layer deeper
  ("DevTools", "GTM/Sales", "Compliance") differentiates a bet.
- **Tech-stack-known-only chart** because rendering "unknown" as the largest
  bar is misleading even when honest. The footnote keeps the honesty.
- **Traction section before spotlights** because traction is a stronger
  signal than capability. A B2B SaaS with 5,000 GitHub stars is more
  interesting than 50 nameless agents companies.

## Citation rules (Layer 2 enforced)

Every number in aggregate prose must trace back to `analytics.headline_numbers`,
a chart counter, or `extra_allowed` (derived sums and infrastructure facts).
Per-company verbatim quotes (taglines, rationales, traction details) are
exempt from drift check but still scanned for forbidden phrases.

The named-figures allowlist (Andreessen, Dalio, Acemoglu) is **explicitly
not** sanitized — these are real public figures whose published views are
being summarized, not anonymous "industry insiders". Per Layer 2 invariants,
the prose around their names must paraphrase rather than fabricate quotes.

## Vetting

The repo's `tests/test_docx.py` exercises:
- The structure renders end-to-end on a synthetic 8-company cohort.
- Layer 2 audit aborts the build on a forbidden-phrase injection.
- Layer 2 audit aborts on a fabricated number injection.
- Sub-industry table appears when B2B SaaS rows exist.
- Tech-stack footnote appears when unknown count > 0.
- Traction section appears when at least one company has signals.
- Three-POV introduction includes the three named figures.

Run via `make validate-p0`.
