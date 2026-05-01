"""Taxonomies + deterministic prefilling.

Where we can answer a classification question from yc-oss/api fields alone
(without an LLM), we do. This:
  1. saves Sonnet calls,
  2. produces a deterministic answer auditors can re-derive,
  3. reduces the surface area where the model can hallucinate.

The LLM still classifies AI capability, tech stack, OSS posture, and the
tagline — fields that can't be derived from YC's tag list.
"""

from __future__ import annotations

from ycai.schemas import Industry

# yc-oss industry / subindustry / tag substrings -> our enum.
# Ordered most-specific first; first match wins.
_INDUSTRY_RULES: tuple[tuple[str, Industry], ...] = (
    ("ai infrastructure", Industry.AI_INFRASTRUCTURE),
    ("developer tools", Industry.DEVELOPER_TOOLS),
    ("dev tools", Industry.DEVELOPER_TOOLS),
    ("security", Industry.SECURITY),
    ("biotech", Industry.BIOTECH),
    ("healthcare", Industry.HEALTHCARE),
    ("medical", Industry.HEALTHCARE),
    ("fintech", Industry.FINTECH),
    ("financial", Industry.FINTECH),
    ("legal", Industry.LEGAL),
    ("education", Industry.EDUCATION),
    ("real estate", Industry.REAL_ESTATE_CONSTRUCTION),
    ("construction", Industry.REAL_ESTATE_CONSTRUCTION),
    ("logistics", Industry.SUPPLY_CHAIN_LOGISTICS),
    ("supply chain", Industry.SUPPLY_CHAIN_LOGISTICS),
    ("climate", Industry.CLIMATE_ENERGY),
    ("energy", Industry.CLIMATE_ENERGY),
    ("robotics", Industry.ROBOTICS),
    ("hardware", Industry.HARDWARE),
    ("industrials", Industry.INDUSTRIALS),
    ("government", Industry.GOVERNMENT_DEFENSE),
    ("defense", Industry.GOVERNMENT_DEFENSE),
    ("media", Industry.MEDIA_CONTENT),
    ("content", Industry.MEDIA_CONTENT),
    ("consumer", Industry.CONSUMER),
    ("b2b", Industry.B2B_SAAS),
    ("saas", Industry.B2B_SAAS),
)


def map_industry(yc_industry: str, yc_subindustry: str = "", yc_tags: list[str] | None = None) -> Industry:
    """Map a yc-oss industry/subindustry/tags hint into our enum.

    Returns ``Industry.UNKNOWN`` only if absolutely nothing matches — the LLM
    can override our guess if it has a stronger signal from the website.
    """
    haystack = " ".join(
        [yc_industry or "", yc_subindustry or "", " ".join(yc_tags or [])],
    ).lower()
    for needle, industry in _INDUSTRY_RULES:
        if needle in haystack:
            return industry
    return Industry.UNKNOWN


def industry_secondaries(yc_industry: str, yc_subindustry: str, yc_tags: list[str]) -> list[Industry]:
    """Extra industry hits beyond the primary, from the same haystack.

    Caps at 3 to keep the chart legible.
    """
    haystack = " ".join([yc_industry or "", yc_subindustry or "", " ".join(yc_tags or [])]).lower()
    seen: list[Industry] = []
    for needle, industry in _INDUSTRY_RULES:
        if needle in haystack and industry not in seen:
            seen.append(industry)
    return seen[1:4]  # skip the primary (index 0), take next 3


__all__ = ["industry_secondaries", "map_industry"]
