"""Pydantic models. Single source of truth for what a company looks like at every stage."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl


class Industry(StrEnum):
    """Closed-set industry labels. Free text is rejected — the LLM must pick one."""

    B2B_SAAS = "B2B SaaS"
    DEVELOPER_TOOLS = "Developer Tools"
    AI_INFRASTRUCTURE = "AI Infrastructure"
    SECURITY = "Security"
    FINTECH = "Fintech"
    HEALTHCARE = "Healthcare"
    BIOTECH = "Biotech"
    INDUSTRIALS = "Industrials"
    ROBOTICS = "Robotics"
    HARDWARE = "Hardware"
    CLIMATE_ENERGY = "Climate / Energy"
    REAL_ESTATE_CONSTRUCTION = "Real Estate / Construction"
    SUPPLY_CHAIN_LOGISTICS = "Supply Chain / Logistics"
    LEGAL = "Legal"
    EDUCATION = "Education"
    CONSUMER = "Consumer"
    MEDIA_CONTENT = "Media / Content"
    GOVERNMENT_DEFENSE = "Government / Defense"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class AICapability(StrEnum):
    """What the company actually does with AI."""

    CODE_GEN = "code-generation"
    AGENTS = "agents"
    RAG = "rag"
    VOICE = "voice"
    VISION = "vision"
    MULTIMODAL = "multimodal"
    NLP_CLASSIC = "nlp-classic"
    SPEECH = "speech"
    ROBOTICS = "robotics"
    BIO_AI = "bio-ai"
    TRAINING_INFRA = "training-infra"
    INFERENCE_INFRA = "inference-infra"
    DATA_PIPELINE = "data-pipeline"
    EVALS_OBSERVABILITY = "evals-observability"
    SAFETY_GUARDRAILS = "safety-guardrails"
    NO_AI = "no-ai"  # explicit signal that this company isn't actually doing AI
    UNCLEAR = "unclear"


class TechStack(StrEnum):
    """Model providers, frameworks, and infra signals visible from public surfaces."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE_GEMINI = "google-gemini"
    META_LLAMA = "meta-llama"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    CUSTOM_MODEL = "custom-model"
    LANGCHAIN = "langchain"
    LLAMAINDEX = "llamaindex"
    VERCEL_AI_SDK = "vercel-ai-sdk"
    HUGGINGFACE = "huggingface"
    ONNX = "onnx"
    PYTORCH = "pytorch"
    JAX = "jax"
    UNKNOWN = "unknown"


class OSSPosture(StrEnum):
    """How open the product or its weights are."""

    FULLY_OPEN = "fully-open"  # source + weights on a permissive license
    WEIGHTS_ONLY = "weights-only"  # weights public, source closed
    SOURCE_AVAILABLE = "source-available"  # source visible, restrictive license
    API_ONLY = "api-only"  # closed product, accessible only via API
    CLOSED = "closed"
    UNKNOWN = "unknown"


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


class CompanyAnalysis(BaseModel):
    """LLM-emitted classification for one company.

    Schema is enforced by pydantic. The model gets one shot to fill this in;
    any failure (missing source, schema violation, JSON parse error) drops the
    row to ``confidence=low`` so it's excluded from charts but listed in the
    audit trail.
    """

    slug: str
    industry_primary: Industry
    industry_secondary: list[Industry] = Field(default_factory=list, max_length=3)
    ai_capability: list[AICapability] = Field(min_length=1, max_length=5)
    tech_stack: list[TechStack] = Field(default_factory=list, max_length=8)
    oss_posture: OSSPosture
    oss_evidence_url: HttpUrl | None = None
    tagline_rewrite: str = Field(min_length=4, max_length=140)
    confidence: Literal["high", "medium", "low"]
    sources: list[HttpUrl] = Field(min_length=1, max_length=10)
    rationale: str = Field(default="", max_length=400)


class CrossCheckResult(BaseModel):
    """Output of the two-pass cross-check on a medium-confidence row."""

    slug: str
    pass_1: CompanyAnalysis
    pass_2: CompanyAnalysis
    agreed_on_industry: bool
    agreed_on_oss: bool
    final_confidence: Literal["high", "medium", "low"]


__all__ = [
    "AICapability",
    "BatchCoverage",
    "CompanyAnalysis",
    "CoverageRecord",
    "CoverageTier",
    "CrossCheckResult",
    "DropReason",
    "HttpUrl",
    "Industry",
    "OSSPosture",
    "RawCompany",
    "TechStack",
]
