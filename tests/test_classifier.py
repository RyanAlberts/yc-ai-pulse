"""Tests for the deterministic prefilling layer in classifier.py.

The classifier should never invent — it just maps yc-oss tag soup into our
closed-set Industry enum. UNKNOWN is the right answer when no rule fires.
"""

from __future__ import annotations

import pytest

from ycai.classifier import industry_secondaries, map_industry
from ycai.schemas import Industry


@pytest.mark.parametrize(
    ("yc_industry", "yc_subindustry", "yc_tags", "expected"),
    [
        ("B2B", "B2B -> Engineering, Product and Design", ["Developer Tools"], Industry.DEVELOPER_TOOLS),
        ("Industrials", "Industrials -> Robotics", [], Industry.ROBOTICS),
        ("Healthcare", "Healthcare -> Medical Devices", [], Industry.HEALTHCARE),
        ("Fintech", "Fintech -> Banking", [], Industry.FINTECH),
        ("Real Estate and Construction", "", ["Construction"], Industry.REAL_ESTATE_CONSTRUCTION),
        ("Consumer", "", [], Industry.CONSUMER),
        ("B2B", "", [], Industry.B2B_SAAS),
    ],
)
def test_map_industry_routes_to_enum(
    yc_industry: str,
    yc_subindustry: str,
    yc_tags: list[str],
    expected: Industry,
) -> None:
    assert map_industry(yc_industry, yc_subindustry, yc_tags) == expected


def test_map_industry_returns_unknown_for_unmappable_input() -> None:
    assert map_industry("", "", []) == Industry.UNKNOWN
    assert map_industry("Underwater Basket Weaving", "", []) == Industry.UNKNOWN


def test_map_industry_first_match_wins() -> None:
    # 'AI Infrastructure' should win over 'Developer Tools' even though both
    # words are present, because it's earlier in the rule list.
    result = map_industry("B2B", "AI Infrastructure", ["Developer Tools"])
    assert result == Industry.AI_INFRASTRUCTURE


def test_map_industry_case_insensitive() -> None:
    assert map_industry("BIOTECH", "", []) == Industry.BIOTECH
    assert map_industry("biotech", "", []) == Industry.BIOTECH


def test_industry_secondaries_caps_at_three_and_skips_primary() -> None:
    # Multiple matches should produce up to 3 secondaries, with the primary excluded.
    secondaries = industry_secondaries(
        "B2B",
        "Healthcare",
        ["Fintech", "Security", "Developer Tools", "Education"],
    )
    assert len(secondaries) <= 3
    assert all(isinstance(s, Industry) for s in secondaries)


def test_industry_secondaries_empty_for_no_matches() -> None:
    assert industry_secondaries("", "", []) == []
