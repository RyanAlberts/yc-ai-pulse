"""Anti-hallucination Layer 2 — applies to deck/memo prose.

Layer 1 (in ``researcher.py``) protects the LLM-emitted CompanyAnalysis rows.
Layer 2 protects the *prose* that surrounds those rows in the deck and the
narrative memo. Two checks:

1. **Forbidden-phrase scan** — block lazy hedges that masquerade as evidence
   (``studies show``, ``experts say``, ``it is widely known``, etc.). If the
   model or a template emits one of these, we refuse to write the artifact.

2. **Numerical-drift check** — every number that appears in prose must trace
   back to the validated DataFrame (``analytics.headline_numbers`` or a chart
   counter). If a sentence says "65% of companies build agents" but the
   underlying counter says 58%, we abort with the offending span.

Both checks return a list of issues; the caller decides whether to abort the
build or warn loudly. Default behavior: any forbidden-phrase hit aborts; a
single numerical drift aborts.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

# Lazy hedges that masquerade as evidence. Case-insensitive whole-phrase match.
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "studies show",
    "research suggests",
    "many believe",
    "experts say",
    "it is widely known",
    "according to industry consensus",
    "as everyone knows",
    "common wisdom",
    "industry insiders",
    "analysts agree",
    "we all know",
)

# Numbers in prose that we extract and validate against the dataframe.
# Plain integers (>=2 digits) and percentages with optional decimal.
_NUMBER_RE = re.compile(r"\b(?P<num>\d{1,4}(?:\.\d{1,2})?)(?P<pct>%)?\b")

# Stripped before number extraction so dates and timestamps don't surface as drift.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"),
    re.compile(r"\b\d{4}-\d{2}\b"),  # YYYY-MM
    re.compile(r"\b(?:in|since|as of) (?:19|20)\d{2}\b", re.IGNORECASE),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+(?:19|20)\d{2}\b"),
)


def _strip_dates(text: str) -> str:
    out = text
    for pattern in _DATE_PATTERNS:
        out = pattern.sub(" ", out)
    return out


@dataclass(frozen=True)
class ForbiddenPhraseHit:
    phrase: str
    excerpt: str  # ~80 chars of context around the hit


@dataclass(frozen=True)
class NumericalDrift:
    number: str  # the literal token from the prose, e.g. "65%"
    excerpt: str
    closest_known: tuple[str, int | float] | None = None  # best match in the DataFrame


@dataclass(frozen=True)
class Layer2Report:
    forbidden: list[ForbiddenPhraseHit]
    drifts: list[NumericalDrift]

    @property
    def is_clean(self) -> bool:
        return not self.forbidden and not self.drifts


def scan_forbidden_phrases(prose: str) -> list[ForbiddenPhraseHit]:
    """Return every forbidden-phrase hit (case-insensitive) with surrounding context."""
    hits: list[ForbiddenPhraseHit] = []
    lower = prose.lower()
    for phrase in FORBIDDEN_PHRASES:
        idx = 0
        while True:
            idx = lower.find(phrase, idx)
            if idx < 0:
                break
            start = max(0, idx - 40)
            end = min(len(prose), idx + len(phrase) + 40)
            hits.append(
                ForbiddenPhraseHit(
                    phrase=phrase,
                    excerpt="..." + prose[start:end].strip() + "..."
                    if start > 0 or end < len(prose)
                    else prose[start:end],
                )
            )
            idx += len(phrase)
    return hits


def _allowed_numbers(
    headline: dict[str, int],
    counters: Iterable[Counter[str]],
    *,
    tolerance_pct: float = 1.0,
) -> set[float]:
    """Build the set of numbers prose is allowed to mention.

    Includes:
      - every integer count from headline + counters
      - the percentage of each count vs. the cohort_size (rounded to nearest 0.5)
    """
    allowed: set[float] = set()
    cohort = headline.get("cohort_size") or 0
    for value in headline.values():
        allowed.add(float(value))
    for counter in counters:
        for value in counter.values():
            allowed.add(float(value))
            if cohort:
                pct = (value / cohort) * 100
                # Allow ±tolerance_pct rounding slop in either direction.
                for p in (round(pct, 0), round(pct, 1)):
                    allowed.add(p)
    # Common stable numbers we know prose tends to cite.
    allowed.add(100.0)  # "100% covered" etc.
    return allowed


def scan_numerical_drift(
    prose: str,
    headline: dict[str, int],
    counters: Iterable[Counter[str]],
    *,
    tolerance_pct: float = 1.0,
    ignore_below: int = 2,
    extra_allowed: Iterable[float] = (),
) -> list[NumericalDrift]:
    """Return every number in ``prose`` that doesn't match an allowed value.

    Numbers below ``ignore_below`` are skipped (no point flagging "1 of 10"
    style phrasing). The check is approximate — we accept a number if it's
    within ``tolerance_pct`` of any allowed percentage.

    ``extra_allowed`` is a stable allowlist for known infrastructure values
    that aren't derived from the data — model version numbers (4.6), crawler
    page caps (5, 30), pipeline byte budgets, etc.
    """
    counters = list(counters)
    allowed = _allowed_numbers(headline, counters, tolerance_pct=tolerance_pct)
    for value in extra_allowed:
        allowed.add(float(value))
    out: list[NumericalDrift] = []
    cleaned = _strip_dates(prose)
    for m in _NUMBER_RE.finditer(cleaned):
        token = m.group(0)
        try:
            num = float(m.group("num"))
        except ValueError:
            continue
        if num < ignore_below:
            continue
        # Direct match? skip.
        if num in allowed:
            continue
        # Close-enough match for percentages?
        if any(abs(num - a) <= tolerance_pct for a in allowed):
            continue
        # Drift detected.
        idx = m.start()
        start = max(0, idx - 40)
        end = min(len(prose), idx + len(token) + 40)
        excerpt = "..." + prose[start:end].strip() + "..." if start > 0 or end < len(prose) else prose[start:end]
        # Find the closest allowed number for the error message.
        closest: tuple[str, int | float] | None = None
        if allowed:
            best = min(allowed, key=lambda a: abs(a - num))
            closest = (token, best)
        out.append(NumericalDrift(number=token, excerpt=excerpt, closest_known=closest))
    return out


def audit(
    prose: str,
    headline: dict[str, int],
    counters: Iterable[Counter[str]],
    *,
    extra_allowed: Iterable[float] = (),
) -> Layer2Report:
    """One-shot Layer 2 audit. Caller checks ``report.is_clean`` and aborts on False."""
    return Layer2Report(
        forbidden=scan_forbidden_phrases(prose),
        drifts=scan_numerical_drift(prose, headline, counters, extra_allowed=extra_allowed),
    )


__all__ = [
    "FORBIDDEN_PHRASES",
    "ForbiddenPhraseHit",
    "Layer2Report",
    "NumericalDrift",
    "audit",
    "scan_forbidden_phrases",
    "scan_numerical_drift",
]
