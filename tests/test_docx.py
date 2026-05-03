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


# ----- PR #17: memo structure invariants ----------------------------------------------------


def test_memo_introduction_includes_three_named_figures(tmp_path: Path) -> None:
    """ADR 0003: every memo opens with the Andreessen / Dalio / Acemoglu frame."""
    coverage = _coverage(tier_a=8)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(8)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Andreessen" in body
    assert "Dalio" in body
    assert "Acemoglu" in body
    assert "Three views" in body or "three views" in body


def test_memo_executive_summary_section_present(tmp_path: Path) -> None:
    coverage = _coverage(tier_a=8)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(8)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Executive summary" in body
    # The Nobel laureate's framing must be cited in the executive summary.
    assert "Acemoglu" in body or "Nobel" in body


def test_memo_named_figures_codified_in_one_place() -> None:
    """The named figures live in a single dict so a maintainer changes one place."""
    from ycai.reports.docx import NAMED_FIGURES

    assert set(NAMED_FIGURES.keys()) == {"andreessen", "dalio", "acemoglu"}
    for figure in NAMED_FIGURES.values():
        assert figure["name"]
        assert figure["affiliation"]
        assert figure["view"]
        assert figure["source"]


def test_memo_renders_subindustry_table_when_b2b_present(tmp_path: Path) -> None:
    from ycai.schemas import Industry

    coverage = _coverage(tier_a=4)
    companies = _companies(coverage)
    # Mix of B2B SaaS sub-industries so the table is non-trivial.
    analyses = [
        _ana("a0", industry_primary=Industry.B2B_SAAS, yc_subindustry="B2B -> Sales"),
        _ana("a1", industry_primary=Industry.B2B_SAAS, yc_subindustry="B2B -> Operations"),
        _ana("a2", industry_primary=Industry.B2B_SAAS, yc_subindustry="B2B -> Sales"),
        _ana("a3", industry_primary=Industry.B2B_SAAS, yc_subindustry="B2B"),
    ]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Inside B2B SaaS" in body
    assert "Sales" in body  # one of the sub-industries
    assert "Sub-industry" in body  # table header


def test_memo_skips_subindustry_section_when_no_b2b(tmp_path: Path) -> None:
    from ycai.schemas import Industry

    coverage = _coverage(tier_a=2)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}", industry_primary=Industry.HEALTHCARE) for i in range(2)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Inside B2B SaaS" not in body


def test_memo_tech_stack_unknown_rendered_as_footnote_not_bar(tmp_path: Path) -> None:
    from ycai.schemas import TechStack

    coverage = _coverage(tier_a=4)
    companies = _companies(coverage)
    analyses = [
        _ana("a0", tech_stack=[TechStack.ANTHROPIC]),
        _ana("a1", tech_stack=[]),  # unknown
        _ana("a2", tech_stack=[]),  # unknown
        _ana("a3", tech_stack=[TechStack.OPENAI]),
    ]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    # The footnote text mentions the unknown count.
    assert "no determinable tech stack" in body
    # Concrete tech-stack entries appear.
    assert "anthropic" in body
    assert "openai" in body


def test_memo_traction_section_present_when_signals_exist(tmp_path: Path) -> None:
    from ycai.schemas import TractionSignal, TractionSignalKind

    coverage = _coverage(tier_a=3)
    companies = _companies(coverage)
    analyses = [
        _ana(
            "a0",
            traction=[
                TractionSignal(
                    kind=TractionSignalKind.CUSTOMER_LOGO,
                    detail="Trusted by Acme Corp and 200 others",
                    source_url="https://a0.example/customers",
                )
            ],
        ),
        _ana("a1"),
        _ana(
            "a2",
            traction=[
                TractionSignal(
                    kind=TractionSignalKind.GITHUB_STARS,
                    detail="3,400 stars on GitHub",
                    source_url="https://a2.example",
                )
            ],
        ),
    ]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Traction signals" in body
    assert "Acme Corp" in body
    assert "github-stars" in body or "GitHub" in body


def test_memo_traction_section_says_so_when_no_signals(tmp_path: Path) -> None:
    coverage = _coverage(tier_a=2)
    companies = _companies(coverage)
    analyses = [_ana(f"a{i}") for i in range(2)]
    out = build_memo(coverage, companies, analyses, output_path=tmp_path / "report.docx")
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Traction signals" in body
    assert "No company" in body or "no traction" in body.lower()
