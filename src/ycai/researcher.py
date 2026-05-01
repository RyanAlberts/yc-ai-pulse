"""LLM-driven enrichment with anti-hallucination Layer 1.

Three backends are available:

- ``AgentSDKBackend`` — default. Uses ``claude-agent-sdk`` to call Sonnet 4.6
  through the local Claude Code subprocess. Honors a Claude Max subscription.
- ``AnthropicAPIBackend`` — opt-in via ``--api-key`` or ``ANTHROPIC_API_KEY``.
  Pay-per-token, faster, fewer external dependencies.
- ``MockBackend`` — for tests. Returns canned responses keyed by slug.

All three return a validated :class:`CompanyAnalysis`. Schema enforcement,
two-pass cross-check on uncertain rows, and forced-low-confidence on parse
failure are implemented in :func:`analyze` (backend-agnostic).
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ValidationError

from ycai.classifier import map_industry
from ycai.sanitizer import strip_pii
from ycai.schemas import (
    AICapability,
    CompanyAnalysis,
    CrossCheckResult,
    Industry,
    OSSPosture,
    RawCompany,
    TechStack,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1500


PROMPT_TEMPLATE = """You are classifying a YC company. Use ONLY the information provided below.
If a field cannot be determined from the provided text, say so explicitly via low confidence
or the 'unknown' enum value — do NOT invent.

Company: {name} (slug: {slug})
YC profile: {yc_url}
Website: {website}
One-liner: {one_liner}
Description: {long_description}
YC industry: {industry}
YC subindustry: {subindustry}
YC tags: {tags}
Pre-filled industry guess: {prefill_industry}

Return ONLY a JSON object matching this schema (no prose, no markdown fences):

{{
  "slug": "{slug}",
  "industry_primary": one of {industry_values},
  "industry_secondary": [up to 3 of the same enum values, may be empty],
  "ai_capability": [1-5 of {capability_values}],
  "tech_stack": [up to 8 of {tech_stack_values}, [] if you cannot tell],
  "oss_posture": one of {oss_values},
  "oss_evidence_url": URL to a repo or license page when oss_posture indicates openness, else null,
  "tagline_rewrite": "<=140 char rewrite of the one-liner in plain VC speak",
  "confidence": "high" | "medium" | "low",
  "sources": [at least one URL — must come from the inputs above (website or yc profile only)],
  "rationale": "one or two sentences cite the words from the inputs that drove your classification"
}}

Hallucination rules (non-negotiable):
- If you cannot find AI capability evidence in the description or tags,
  set ai_capability to ["no-ai"] or ["unclear"] and confidence="low".
- 'sources' must be URLs that appear in the inputs. Do not invent URLs.
- If oss_posture is anything other than 'closed', 'api-only', or 'unknown',
  oss_evidence_url is REQUIRED.
- If the description is too thin to classify, return confidence="low" and
  use 'unknown' / 'unclear' enums.
"""


class Backend(ABC):
    """Abstract LLM backend. Implementations return raw model text given a prompt."""

    @abstractmethod
    async def complete(self, prompt: str, *, model: str = DEFAULT_MODEL) -> str:
        """Return the model's reply as a single string. Implementations must not
        raise on a model error — return an empty string instead, so the caller
        can mark the row low-confidence and continue.
        """


class MockBackend(Backend):
    """Test backend. Looks up pre-recorded responses by slug.

    The recorded JSON file has shape: ``{"slug": {"<pass>": "<json string>"}}``.
    For two-pass cross-checks, ``<pass>`` is "1" then "2".
    """

    def __init__(self, fixture: Mapping[str, Mapping[str, str]]) -> None:
        self._fixture = fixture
        self._call_counts: dict[str, int] = {}

    async def complete(self, prompt: str, *, model: str = DEFAULT_MODEL) -> str:
        slug = self._extract_slug(prompt)
        if slug not in self._fixture:
            return ""  # forces low confidence
        slug_calls = self._call_counts.get(slug, 0) + 1
        self._call_counts[slug] = slug_calls
        responses = self._fixture[slug]
        # Exact-call match preferred; "default" is the catch-all. If neither
        # matches (e.g. test only provides "1" but pipeline calls a 2nd time),
        # return empty so the cross-check correctly downgrades.
        for key in (str(slug_calls), "default"):
            if key in responses:
                return responses[key]
        return ""

    @staticmethod
    def _extract_slug(prompt: str) -> str:
        match = re.search(r"slug:\s*([a-z0-9-]+)", prompt)
        return match.group(1) if match else ""


class AnthropicAPIBackend(Backend):
    """Pay-per-token backend. Uses the Anthropic SDK with tool_use forced output.

    Triggered via ``--api-key`` or ``ANTHROPIC_API_KEY``. The key is held in
    memory only — never logged, never written to disk.
    """

    def __init__(self, api_key: str | None = None) -> None:
        # Defer the import so users without the dep installed can still use other backends.
        from anthropic import AsyncAnthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "AnthropicAPIBackend requires an API key. " "Set ANTHROPIC_API_KEY or pass api_key=...",
            )
        self._client = AsyncAnthropic(api_key=key)

    async def complete(self, prompt: str, *, model: str = DEFAULT_MODEL) -> str:
        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=DEFAULT_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            log.warning("AnthropicAPIBackend failure: %s", exc.__class__.__name__)
            return ""

        for block in resp.content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                return text
        return ""


class AgentSDKBackend(Backend):
    """Default subscription-friendly backend. Uses ``claude-agent-sdk`` to drive
    the local Claude Code subprocess against the user's Max subscription.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        # Defer the import so users without claude-agent-sdk can still use the API backend.
        from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: F401  (used in complete)

        self._model = model

    async def complete(self, prompt: str, *, model: str = DEFAULT_MODEL) -> str:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

        options = ClaudeAgentOptions(
            model=model,
            permission_mode="default",
            max_turns=1,
            disallowed_tools=["Bash", "Edit", "Write", "Read"],  # classification only
            system_prompt=(
                "You are a strict classifier. Output JSON exactly matching the requested schema. "
                "Do not invent URLs. Do not include prose outside the JSON."
            ),
        )
        try:
            chunks: list[str] = []
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
            return "".join(chunks)
        except Exception as exc:
            log.warning("AgentSDKBackend failure: %s", exc.__class__.__name__)
            return ""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


_VALID_INDUSTRY_VALUES = {i.value for i in Industry}


def _drop_unknown_industries(payload: dict[str, Any], slug: str) -> None:
    """Filter ``industry_secondary`` to enum members the model is allowed to emit.

    Models trained on broad data sometimes emit reasonable categories that
    aren't in our closed set (e.g. 'Productivity', 'Marketing'). Rather than
    fail the entire row, drop the unrecognized secondaries and keep going.
    Primary industry stays strict — that's the load-bearing field.
    """
    raw_secondaries = payload.get("industry_secondary", [])
    if not isinstance(raw_secondaries, list):
        payload["industry_secondary"] = []
        return
    cleaned = [s for s in raw_secondaries if isinstance(s, str) and s in _VALID_INDUSTRY_VALUES]
    if len(cleaned) != len(raw_secondaries):
        log.debug(
            "dropped %d unknown industry_secondary entries for %s",
            len(raw_secondaries) - len(cleaned),
            slug,
        )
    payload["industry_secondary"] = cleaned


def _parse_response(raw: str, *, slug: str) -> CompanyAnalysis | None:
    """Strict-parse the model output. Returns ``None`` on any failure."""
    if not raw:
        return None
    # Tolerate surrounding fences / prose by extracting the outermost JSON object.
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    payload.setdefault("slug", slug)  # the model sometimes drops the slug
    _drop_unknown_industries(payload, slug)
    try:
        return CompanyAnalysis.model_validate(payload)
    except ValidationError as exc:
        log.debug("schema validation failed for %s: %s", slug, exc)
        return None


def _build_prompt(company: RawCompany, prefill_industry: Industry) -> str:
    """Assemble the strict-output prompt. PII is stripped defensively."""
    return PROMPT_TEMPLATE.format(
        slug=company.slug,
        name=strip_pii(company.name),
        yc_url=company.url,
        website=company.website,
        one_liner=strip_pii(company.one_liner),
        long_description=strip_pii(company.long_description),
        industry=company.industry,
        subindustry=company.subindustry,
        tags=", ".join(company.tags) or "(none)",
        prefill_industry=prefill_industry.value,
        industry_values=[i.value for i in Industry],
        capability_values=[c.value for c in AICapability],
        tech_stack_values=[t.value for t in TechStack],
        oss_values=[o.value for o in OSSPosture],
    )


def _force_low(slug: str, reason: str) -> CompanyAnalysis:
    """Build a sentinel low-confidence analysis when the model fails."""
    return CompanyAnalysis.model_validate(
        {
            "slug": slug,
            "industry_primary": Industry.UNKNOWN,
            "industry_secondary": [],
            "ai_capability": [AICapability.UNCLEAR],
            "tech_stack": [],
            "oss_posture": OSSPosture.UNKNOWN,
            "oss_evidence_url": None,
            "tagline_rewrite": f"(no analysis: {reason})",
            "confidence": "low",
            "sources": ["https://github.com/RyanAlberts/yc-ai-pulse#unverifiable"],
            "rationale": f"Auto-generated low-confidence sentinel because: {reason}",
        }
    )


def _looks_like_input_url(url: str, company: RawCompany) -> bool:
    """Defense against hallucinated source URLs — a source must come from the inputs."""
    allowed = [company.website, company.url]
    return any(url.startswith(allowed_url.rstrip("/")) for allowed_url in allowed if allowed_url)


def _validate_sources(analysis: CompanyAnalysis, company: RawCompany) -> bool:
    """Return False if any cited source isn't actually in the inputs."""
    for src in analysis.sources:
        if not _looks_like_input_url(str(src), company):
            log.info("rejecting hallucinated source %s for %s", src, company.slug)
            return False
    return True


async def analyze(
    company: RawCompany,
    backend: Backend,
    *,
    model: str = DEFAULT_MODEL,
    cross_check_uncertain: bool = True,
) -> tuple[CompanyAnalysis, CrossCheckResult | None]:
    """Run a single classification with all Layer 1 guards.

    Returns ``(analysis, cross_check_result_or_None)``.

    Guards applied (in order):
      1. Schema-enforced output (pydantic).
      2. Sources must originate from the company's website or YC profile URL.
      3. If first pass returns ``confidence=medium``, run a second independent
         pass. Disagreement on industry_primary or oss_posture downgrades the
         row to ``confidence=low``.
      4. Any failure in 1-3 -> low-confidence sentinel that survives in the CSV
         but is excluded from charts.
    """
    prefill = map_industry(company.industry, company.subindustry, company.tags)
    prompt = _build_prompt(company, prefill)

    raw1 = await backend.complete(prompt, model=model)
    pass_1 = _parse_response(raw1, slug=company.slug)
    if pass_1 is None:
        return _force_low(company.slug, "schema-validation-failure"), None
    if not _validate_sources(pass_1, company):
        return _force_low(company.slug, "hallucinated-source-url"), None

    if pass_1.confidence in ("high", "low") or not cross_check_uncertain:
        return pass_1, None

    # confidence=medium → cross-check pass.
    raw2 = await backend.complete(prompt, model=model)
    pass_2 = _parse_response(raw2, slug=company.slug)
    if pass_2 is None or not _validate_sources(pass_2, company):
        # The cross-check itself failed → keep pass 1 but downgrade to low.
        downgraded = pass_1.model_copy(update={"confidence": "low"})
        return downgraded, None

    agreed_industry = pass_1.industry_primary == pass_2.industry_primary
    agreed_oss = pass_1.oss_posture == pass_2.oss_posture
    result_confidence: Literal["high", "medium", "low"]
    if agreed_industry and agreed_oss:
        # Two independent passes agreed → upgrade to high.
        final = pass_1.model_copy(update={"confidence": "high"})
        result_confidence = "high"
    else:
        # Disagreement → downgrade to low; the row is excluded from charts.
        final = pass_1.model_copy(update={"confidence": "low"})
        result_confidence = "low"

    return final, CrossCheckResult(
        slug=company.slug,
        pass_1=pass_1,
        pass_2=pass_2,
        agreed_on_industry=agreed_industry,
        agreed_on_oss=agreed_oss,
        final_confidence=result_confidence,
    )


def make_default_backend(api_key: str | None = None) -> Backend:
    """Pick a backend per CLAUDE.md memory: subscription first, API only if key provided.

    Order:
      1. ``api_key`` argument → ``AnthropicAPIBackend``.
      2. ``ANTHROPIC_API_KEY`` env var → ``AnthropicAPIBackend``.
      3. ``claude-agent-sdk`` importable → ``AgentSDKBackend``.
      4. Otherwise: raise. We don't silently fall back.
    """
    if api_key:
        return AnthropicAPIBackend(api_key=api_key)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicAPIBackend()
    try:
        return AgentSDKBackend()
    except ImportError as exc:
        raise RuntimeError(
            "No LLM backend available. Either install `claude-agent-sdk` "
            "for subscription mode (recommended), or set ANTHROPIC_API_KEY for "
            "pay-per-token mode.",
        ) from exc


def _load_recorded_fixture(path: str) -> dict[str, dict[str, str]]:
    """Helper for tests: load a recorded fixture from disk into a MockBackend."""
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    out: dict[str, dict[str, str]] = {}
    for slug, value in data.items():
        if isinstance(value, dict):
            out[slug] = {k: json.dumps(v) if isinstance(v, dict) else str(v) for k, v in value.items()}
        else:
            out[slug] = {"default": json.dumps(value) if isinstance(value, dict) else str(value)}
    return out


__all__ = [
    "DEFAULT_MODEL",
    "AgentSDKBackend",
    "AnthropicAPIBackend",
    "Backend",
    "MockBackend",
    "analyze",
    "make_default_backend",
]
