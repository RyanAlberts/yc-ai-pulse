"""Tests for the Layer 2 anti-hallucination scanner."""

from __future__ import annotations

from collections import Counter

from ycai.reports.anti_hallucination import (
    audit,
    scan_forbidden_phrases,
    scan_numerical_drift,
)


def test_forbidden_phrase_scan_catches_studies_show() -> None:
    prose = "Studies show that 65% of YC W26 companies build agents."
    hits = scan_forbidden_phrases(prose)
    assert any(h.phrase == "studies show" for h in hits)


def test_forbidden_phrase_scan_is_case_insensitive() -> None:
    prose = "Experts Say agents are the future."
    hits = scan_forbidden_phrases(prose)
    assert any(h.phrase == "experts say" for h in hits)


def test_forbidden_phrase_scan_clean_prose_returns_empty() -> None:
    prose = "Of the 124 W26 companies, 68 build agents according to our analysis."
    hits = scan_forbidden_phrases(prose)
    assert hits == []


def test_numerical_drift_passes_for_exact_values() -> None:
    headline = {"cohort_size": 124, "high_confidence": 118, "agents_count": 68}
    counters = [Counter({"agents": 68, "rag": 30})]
    prose = "Of 124 companies, 118 were high-confidence, and 68 build agents."
    drifts = scan_numerical_drift(prose, headline, counters)
    assert drifts == []


def test_numerical_drift_catches_invented_numbers() -> None:
    headline = {"cohort_size": 124, "agents_count": 68}
    counters = [Counter({"agents": 68})]
    prose = "Roughly 200 of the YC companies are agents-first."
    drifts = scan_numerical_drift(prose, headline, counters)
    assert any("200" in d.number for d in drifts)


def test_numerical_drift_accepts_percentages_within_tolerance() -> None:
    """If cohort=118 and agents=68, the model can say '58%' — that's exact rounding."""
    headline = {"cohort_size": 118, "agents_count": 68}
    counters = [Counter({"agents": 68})]
    prose = "58% of the cohort builds agents."
    drifts = scan_numerical_drift(prose, headline, counters, tolerance_pct=1.0)
    assert drifts == []


def test_numerical_drift_skips_small_numbers() -> None:
    """'1 of 10' style phrasing should not be flagged as drift."""
    headline = {"cohort_size": 100}
    counters = [Counter({"agents": 50})]
    prose = "We dropped 1 row that violated the schema."
    drifts = scan_numerical_drift(prose, headline, counters, ignore_below=2)
    assert drifts == []


def test_audit_combines_forbidden_and_drift() -> None:
    headline = {"cohort_size": 100, "agents": 50}
    counters = [Counter({"agents": 50})]
    prose = "Studies show that 87 companies are agents-first."
    report = audit(prose, headline, counters)
    assert not report.is_clean
    assert len(report.forbidden) == 1
    assert any("87" in d.number for d in report.drifts)


def test_audit_clean_prose_passes() -> None:
    headline = {"cohort_size": 124, "high_confidence": 118, "agents_count": 68}
    counters = [Counter({"agents": 68})]
    prose = "Of 124 companies, 118 were high-confidence, and 68 build agents."
    report = audit(prose, headline, counters)
    assert report.is_clean


def test_drift_excerpt_includes_surrounding_context() -> None:
    headline = {"cohort_size": 100}
    counters = [Counter({"agents": 50})]
    prose = "The W26 batch had 200 companies in some other dataset."
    drifts = scan_numerical_drift(prose, headline, counters)
    assert drifts
    assert "200" in drifts[0].excerpt
