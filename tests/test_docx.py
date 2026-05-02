"""Smoke tests for the .docx memo builder."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tests.test_ppt import _ana, _companies, _coverage
from ycai.reports.docx import build_memo
from ycai.reports.ppt import Layer2Failure


def test_build_memo_produces_valid_docx(tmp_path: Path) -> None:
    coverage = _coverage(tier_a=8)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(8)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    assert out.exists()
    assert out.stat().st_size > 5_000
    # .docx files are valid ZIPs.
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any("word/document.xml" in n for n in names)
        # Must contain at least 3 embedded chart images (heatmap, industry bar, OSS pie, stack bar).
        media_count = sum(1 for n in names if "word/media/image" in n)
        assert media_count >= 3


def test_build_memo_includes_headline_pct_in_document_xml(tmp_path: Path) -> None:
    coverage = _coverage(tier_a=8)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(8)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "coverage" in body.lower()
    assert "agents" in body.lower()


def test_build_memo_aborts_on_forbidden_phrase_in_quoted_facts(tmp_path: Path) -> None:
    """A poisoned company rationale containing a forbidden phrase aborts the build."""
    coverage = _coverage(tier_a=3)
    companies = _companies(coverage)
    poisoned = _ana(
        "poisoned",
        rationale="Studies show that this company is doing important work.",
    )
    analyses = [_ana("a0"), _ana("a1"), poisoned]
    with pytest.raises(Layer2Failure) as excinfo:
        build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    assert any(h.phrase == "studies show" for h in excinfo.value.forbidden)


def test_build_memo_handles_empty_quote_list(tmp_path: Path) -> None:
    """If no companies qualify as quote candidates (taglines too short), memo still builds."""
    coverage = _coverage(tier_a=2)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}", tagline_rewrite="Short.") for i in range(2)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    assert out.exists()
