"""Pydantic models. Single source of truth for what a company looks like at every stage."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl


class CoverageTier(StrEnum):
    """How completely we can analyze a company.

    A: full — every required field present, website reachable.
    B: partial — core fields present, website unreachable (analysis flagged).
    C: excluded — missing critical fields, listed in the dropped register, no charts.
    """

    A = "A"
    B = "B"
    C = "C"


class DropReason(StrEnum):
    NO_WEBSITE = "no_website"
    NO_DESCRIPTION = "no_description"
    NO_INDUSTRY = "no_industry"
    INVALID_SLUG = "invalid_slug"
    INACTIVE = "inactive"
    DUPLICATE = "duplicate"


class RawCompany(BaseModel):
    """Mirrors the yc-oss/api batch schema. Fields beyond the ones we use are dropped."""

    slug: str
    name: str
    batch: str
    website: str = ""
    one_liner: str = ""
    long_description: str = ""
    industry: str = ""
    industries: list[str] = Field(default_factory=list)
    subindustry: str = ""
    tags: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    team_size: int | None = None
    status: str = ""
    stage: str = ""
    top_company: bool = False
    url: str = ""  # yc.com profile URL
    launched_at: int | None = None
    isHiring: bool = False


class CoverageRecord(BaseModel):
    """Per-company outcome of the coverage probe.

    A row exists for every company in the source batch — Tier A/B rows feed the
    charts; Tier C rows feed the dropped register so coverage is honest.
    """

    slug: str
    name: str
    tier: CoverageTier
    drop_reasons: list[DropReason] = Field(default_factory=list)
    website_status: str | None = None  # 'ok' | 'dead' | 'slow' | 'redirect' | None
    notes: list[str] = Field(default_factory=list)


class BatchCoverage(BaseModel):
    """Aggregate coverage for a single run."""

    batch_slug: str  # 'winter-2026'
    batch_label: str  # 'Winter 2026'
    source: str  # 'yc-oss/api'
    source_last_updated: datetime | None
    fetched_at: datetime
    upstream_company_count: int
    yc_official_count: int | None  # if known (e.g. from demo-day report); else None
    tier_a_count: int
    tier_b_count: int
    tier_c_count: int
    records: list[CoverageRecord]

    @property
    def analyzable_count(self) -> int:
        return self.tier_a_count + self.tier_b_count

    @property
    def coverage_pct_of_upstream(self) -> float:
        if self.upstream_company_count == 0:
            return 0.0
        return round(100.0 * self.analyzable_count / self.upstream_company_count, 1)

    @property
    def coverage_pct_of_official(self) -> float | None:
        """Coverage relative to YC's known batch size (if we have it).

        This is the headline number the user actually cares about: "how much of
        the actual batch did we cover?" — answers both upstream-staleness and
        per-company quality issues.
        """
        if self.yc_official_count is None or self.yc_official_count == 0:
            return None
        return round(100.0 * self.analyzable_count / self.yc_official_count, 1)


HttpUrlStr = Annotated[str, Field(min_length=1)]
__all__ = [
    "BatchCoverage",
    "CoverageRecord",
    "CoverageTier",
    "DropReason",
    "HttpUrl",
    "RawCompany",
]
