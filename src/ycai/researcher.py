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
from datetime import UTC, datetime
from pathlib import Path
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

{crawl_section}
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
_VALID_CAPABILITY_VALUES = {c.value for c in AICapability}
_VALID_STACK_VALUES = {s.value for s in TechStack}
_VALID_OSS_VALUES = {o.value for o in OSSPosture}


def _filter_list(payload: dict[str, Any], key: str, valid: set[str], *, fallback: list[str] | None = None) -> int:
    """Drop list entries that aren't in ``valid``. If filtering empties the list
    and a ``fallback`` is provided, set it. Returns the count dropped."""
    raw = payload.get(key, [])
    if not isinstance(raw, list):
        payload[key] = fallback or []
        return 0
    cleaned = [item for item in raw if isinstance(item, str) and item in valid]
    dropped = len(raw) - len(cleaned)
    if not cleaned and fallback is not None:
        cleaned = list(fallback)
    payload[key] = cleaned
    return dropped


_RATIONALE_MAX = 400
_TAGLINE_MAX = 140


def _normalize_payload(payload: dict[str, Any], slug: str) -> None:
    """Apply lenient filters and normalizations the model occasionally trips on.

    What stays strict (rejection on bad value):
      - ``industry_primary``
      - ``oss_posture``
      - ``confidence``
      - ``sources`` (must be valid HttpUrls, >=1 entry)

    What gets leniently filtered:
      - ``industry_secondary`` (drop unknown values; empty list is fine)
      - ``ai_capability`` (drop unknown values; falls back to ['unclear'] if emptied)
      - ``tech_stack`` (drop unknown values; empty list is fine)
      - ``rationale`` (truncated at 400 chars rather than failing the row)
      - ``tagline_rewrite`` (truncated at 140 chars rather than failing the row)
    """
    payload.setdefault("slug", slug)
    drops_total = 0
    drops_total += _filter_list(payload, "industry_secondary", _VALID_INDUSTRY_VALUES)
    drops_total += _filter_list(
        payload, "ai_capability", _VALID_CAPABILITY_VALUES, fallback=[AICapability.UNCLEAR.value]
    )
    drops_total += _filter_list(payload, "tech_stack", _VALID_STACK_VALUES)
    if drops_total:
        log.debug("dropped %d unknown enum value(s) from %s payload", drops_total, slug)
    # Truncate verbose free-text fields. The model often pushes past these
    # caps when describing complex products; truncation is safer than dropping
    # the whole classification.
    rationale = payload.get("rationale")
    if isinstance(rationale, str) and len(rationale) > _RATIONALE_MAX:
        payload["rationale"] = rationale[: _RATIONALE_MAX - 1] + "…"
    tagline = payload.get("tagline_rewrite")
    if isinstance(tagline, str) and len(tagline) > _TAGLINE_MAX:
        payload["tagline_rewrite"] = tagline[: _TAGLINE_MAX - 1] + "…"


def _parse_response(raw: str, *, slug: str) -> CompanyAnalysis | None:
    """Strict-parse the model output. Returns ``None`` on any failure."""
    if not raw:
        return None
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    _normalize_payload(payload, slug)
    try:
        return CompanyAnalysis.model_validate(payload)
    except ValidationError as exc:
        log.debug("schema validation failed for %s: %s", slug, exc)
        return None


def _build_crawl_section(crawled_pages: list[tuple[str, str]] | None, char_budget: int = 6000) -> str:
    """Assemble the crawled-context section. Empty string when nothing crawled.

    ``crawled_pages`` is a list of ``(url, sanitized_text)`` pairs. We cap the
    total characters fed to the model to keep prompts predictable.
    """
    if not crawled_pages:
        return ""
    out: list[str] = ["Additional evidence from a polite depth=1 crawl of the company website:"]
    remaining = char_budget
    for url, text in crawled_pages:
        if remaining <= 100:
            break
        snippet = text[: min(remaining, 1500)]
        out.append(f"\n--- Crawled page: {url} ---\n{snippet}")
        remaining -= len(snippet)
    out.append(
        "\nWhen citing tech_stack, oss_posture, or pricing in your sources, prefer the"
        " specific crawled URL above that contains the evidence (e.g., the /pricing or"
        " /security page) over the bare homepage."
    )
    return "\n".join(out) + "\n"


def _build_prompt(
    company: RawCompany,
    prefill_industry: Industry,
    crawled_pages: list[tuple[str, str]] | None = None,
) -> str:
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
        crawl_section=_build_crawl_section(crawled_pages),
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


def _looks_like_input_url(url: str, company: RawCompany, extra_allowed: list[str] | None = None) -> bool:
    """Defense against hallucinated source URLs — a source must come from the inputs.

    ``extra_allowed`` is the list of URLs reached via the depth=1 crawl. The
    model is allowed to cite any of them.
    """
    allowed = [company.website, company.url, *(extra_allowed or [])]
    return any(url.startswith(allowed_url.rstrip("/")) for allowed_url in allowed if allowed_url)


def _validate_sources(analysis: CompanyAnalysis, company: RawCompany, extra_allowed: list[str] | None = None) -> bool:
    """Return False if any cited source isn't actually in the inputs or crawl."""
    for src in analysis.sources:
        if not _looks_like_input_url(str(src), company, extra_allowed=extra_allowed):
            log.info("rejecting hallucinated source %s for %s", src, company.slug)
            return False
    return True


async def analyze(
    company: RawCompany,
    backend: Backend,
    *,
    model: str = DEFAULT_MODEL,
    cross_check_uncertain: bool = True,
    raw_failure_log: Path | None = None,
    crawled_pages: list[tuple[str, str]] | None = None,
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

    If ``raw_failure_log`` is set, raw model responses for any failure are
    appended (one JSON line per record) so they can be audited / replayed.
    """
    prefill = map_industry(company.industry, company.subindustry, company.tags)
    prompt = _build_prompt(company, prefill, crawled_pages=crawled_pages)
    crawled_urls = [url for url, _ in crawled_pages] if crawled_pages else None

    raw1 = await backend.complete(prompt, model=model)
    pass_1 = _parse_response(raw1, slug=company.slug)
    if pass_1 is None:
        _log_raw_failure(raw_failure_log, slug=company.slug, reason="schema-validation-failure", raw=raw1)
        return _force_low(company.slug, "schema-validation-failure"), None
    if not _validate_sources(pass_1, company, extra_allowed=crawled_urls):
        _log_raw_failure(raw_failure_log, slug=company.slug, reason="hallucinated-source-url", raw=raw1)
        return _force_low(company.slug, "hallucinated-source-url"), None

    if pass_1.confidence in ("high", "low") or not cross_check_uncertain:
        return pass_1, None

    # confidence=medium → cross-check pass.
    raw2 = await backend.complete(prompt, model=model)
    pass_2 = _parse_response(raw2, slug=company.slug)
    if pass_2 is None or not _validate_sources(pass_2, company, extra_allowed=crawled_urls):
        # The cross-check itself failed → keep pass 1 but downgrade to low.
        _log_raw_failure(raw_failure_log, slug=company.slug, reason="cross-check-failed", raw=raw2)
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


def _log_raw_failure(path: Path | None, *, slug: str, reason: str, raw: str) -> None:
    """Append a raw failure record to ``path`` (JSONL). No-op when path is None.

    Truncates raw payloads at 4000 chars so the file stays small but a
    representative sample is captured for B008-style debugging.
    """
    if path is None:
        return
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "slug": slug,
        "reason": reason,
        "raw": (raw or "")[:4000],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


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
