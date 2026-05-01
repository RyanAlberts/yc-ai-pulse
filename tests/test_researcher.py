"""Tests for ycai.researcher — schema enforcement, source validation,
two-pass cross-check, and the hallucination-trap regression suite.

No real LLM calls. Everything routes through ``MockBackend`` with canned
responses, so this file is fully deterministic.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ycai.researcher import (
    AnthropicAPIBackend,
    MockBackend,
    _force_low,
    _parse_response,
    _validate_sources,
    analyze,
    make_default_backend,
)
from ycai.schemas import (
    AICapability,
    CompanyAnalysis,
    Industry,
    OSSPosture,
    RawCompany,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _good_response(slug: str, *, confidence: str = "high", website: str = "https://acme.ai") -> str:
    """Build a valid response payload for slug, anchored at the given website."""
    return json.dumps(
        {
            "slug": slug,
            "industry_primary": "B2B SaaS",
            "industry_secondary": ["Developer Tools"],
            "ai_capability": ["agents", "code-generation"],
            "tech_stack": ["anthropic", "langchain"],
            "oss_posture": "api-only",
            "oss_evidence_url": None,
            "tagline_rewrite": "Anthropic-powered code agent for engineering teams.",
            "confidence": confidence,
            "sources": [website, f"https://www.ycombinator.com/companies/{slug}"],
            "rationale": "Description explicitly mentions code-generation agents using Claude.",
        }
    )


def _make_company(slug: str = "acme-ai", website: str = "https://acme.ai") -> RawCompany:
    return RawCompany.model_validate(
        {
            "slug": slug,
            "name": "Acme AI",
            "batch": "Winter 2026",
            "website": website,
            "url": f"https://www.ycombinator.com/companies/{slug}",
            "one_liner": "AI agents for engineers",
            "long_description": "Acme AI builds autonomous code-generation agents on Anthropic's Claude.",
            "industry": "B2B",
            "industries": ["B2B"],
            "tags": ["AI", "Developer Tools"],
        }
    )


# ----- _parse_response: schema enforcement ----------------------------------------------------


def test_parse_response_strict_validates_required_fields() -> None:
    raw = _good_response("acme-ai")
    out = _parse_response(raw, slug="acme-ai")
    assert isinstance(out, CompanyAnalysis)
    assert out.confidence == "high"
    assert out.industry_primary == Industry.B2B_SAAS
    assert AICapability.AGENTS in out.ai_capability


def test_parse_response_rejects_empty_sources() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["sources"] = []
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is None


def test_parse_response_rejects_invalid_industry() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["industry_primary"] = "Made-up Vertical"
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is None


def test_parse_response_handles_fenced_output() -> None:
    raw = "Sure, here you go:\n\n```json\n" + _good_response("acme-ai") + "\n```\n"
    out = _parse_response(raw, slug="acme-ai")
    assert out is not None


def test_parse_response_handles_empty_string() -> None:
    assert _parse_response("", slug="acme-ai") is None


def test_parse_response_handles_garbage() -> None:
    assert _parse_response("hello world I am the model", slug="acme-ai") is None


def test_parse_response_supplies_missing_slug() -> None:
    payload = json.loads(_good_response("acme-ai"))
    del payload["slug"]
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is not None
    assert out.slug == "acme-ai"


# ----- _validate_sources: hallucinated-URL guard ----------------------------------------------


def test_validate_sources_accepts_input_urls() -> None:
    company = _make_company()
    analysis = _parse_response(_good_response("acme-ai"), slug="acme-ai")
    assert analysis is not None
    assert _validate_sources(analysis, company) is True


def test_validate_sources_rejects_invented_urls() -> None:
    company = _make_company()
    payload = json.loads(_good_response("acme-ai"))
    payload["sources"] = ["https://invented.example/research"]
    analysis = _parse_response(json.dumps(payload), slug="acme-ai")
    assert analysis is not None
    assert _validate_sources(analysis, company) is False


# ----- analyze() with MockBackend: end-to-end flow --------------------------------------------


def test_analyze_returns_high_confidence_on_valid_response() -> None:
    backend = MockBackend({"acme-ai": {"1": _good_response("acme-ai")}})
    company = _make_company()
    analysis, cross = asyncio.run(analyze(company, backend))
    assert analysis.confidence == "high"
    assert cross is None  # no cross-check on high confidence


def test_analyze_drops_to_low_on_empty_response() -> None:
    backend = MockBackend({})  # no fixtures → empty response
    analysis, cross = asyncio.run(analyze(_make_company(), backend))
    assert analysis.confidence == "low"
    assert "no analysis" in analysis.tagline_rewrite
    assert cross is None


def test_analyze_drops_to_low_on_hallucinated_source() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["sources"] = ["https://made-up.example"]
    backend = MockBackend({"acme-ai": {"1": json.dumps(payload)}})
    analysis, cross = asyncio.run(analyze(_make_company(), backend))
    assert analysis.confidence == "low"
    assert "hallucinated-source-url" in analysis.tagline_rewrite
    assert cross is None


# ----- two-pass cross-check ---------------------------------------------------------------


def test_cross_check_upgrades_to_high_on_agreement() -> None:
    medium_pass1 = json.loads(_good_response("acme-ai"))
    medium_pass1["confidence"] = "medium"
    medium_pass2 = json.loads(_good_response("acme-ai"))
    medium_pass2["confidence"] = "medium"  # same industry & oss → agree
    backend = MockBackend({"acme-ai": {"1": json.dumps(medium_pass1), "2": json.dumps(medium_pass2)}})
    analysis, cross = asyncio.run(analyze(_make_company(), backend))
    assert analysis.confidence == "high"
    assert cross is not None
    assert cross.agreed_on_industry is True
    assert cross.agreed_on_oss is True
    assert cross.final_confidence == "high"


def test_cross_check_downgrades_to_low_on_disagreement() -> None:
    pass1 = json.loads(_good_response("acme-ai"))
    pass1["confidence"] = "medium"
    pass2 = json.loads(_good_response("acme-ai"))
    pass2["confidence"] = "medium"
    pass2["industry_primary"] = "Healthcare"  # disagree on industry
    backend = MockBackend({"acme-ai": {"1": json.dumps(pass1), "2": json.dumps(pass2)}})
    analysis, cross = asyncio.run(analyze(_make_company(), backend))
    assert analysis.confidence == "low"
    assert cross is not None
    assert cross.agreed_on_industry is False
    assert cross.final_confidence == "low"


def test_cross_check_failed_pass_2_downgrades_to_low() -> None:
    pass1 = json.loads(_good_response("acme-ai"))
    pass1["confidence"] = "medium"
    backend = MockBackend({"acme-ai": {"1": json.dumps(pass1)}})  # No "2" key → second pass returns ""
    analysis, _ = asyncio.run(analyze(_make_company(), backend))
    assert analysis.confidence == "low"


def test_cross_check_disabled_keeps_medium() -> None:
    pass1 = json.loads(_good_response("acme-ai"))
    pass1["confidence"] = "medium"
    backend = MockBackend({"acme-ai": {"1": json.dumps(pass1)}})
    analysis, cross = asyncio.run(analyze(_make_company(), backend, cross_check_uncertain=False))
    assert analysis.confidence == "medium"
    assert cross is None


# ----- _force_low sentinel ---------------------------------------------------------------


def test_force_low_returns_low_confidence_with_safe_fields() -> None:
    sentinel = _force_low("acme-ai", "test-reason")
    assert sentinel.confidence == "low"
    assert sentinel.industry_primary == Industry.UNKNOWN
    assert sentinel.ai_capability == [AICapability.UNCLEAR]
    assert sentinel.oss_posture == OSSPosture.UNKNOWN
    assert "test-reason" in sentinel.rationale


# ----- backend selection logic ---------------------------------------------------------------


def test_make_default_backend_with_api_key_returns_api_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # pragma: allowlist secret
    backend = make_default_backend(api_key="sk-ant-test-fixture-not-real")
    assert isinstance(backend, AnthropicAPIBackend)


def test_make_default_backend_falls_through_to_agent_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend = make_default_backend()
    # Either AgentSDKBackend (claude-agent-sdk installed) or RuntimeError raised earlier.
    # If we got here, it must be the SDK backend.
    from ycai.researcher import AgentSDKBackend

    assert isinstance(backend, AgentSDKBackend)


def test_api_backend_does_not_persist_key_anywhere(tmp_path: Path) -> None:
    """If a user passes --api-key, the value must never end up on disk."""
    # pragma: allowlist secret
    fake_key = "sk-ant-NEVER-WRITTEN-TO-DISK"
    backend = AnthropicAPIBackend(api_key=fake_key)
    # Inspect the backend's repr / public attrs — the key must not be exposed.
    assert fake_key not in repr(backend)
    # Confirm there's no attribute path that exposes it directly.
    public_attrs = [a for a in dir(backend) if not a.startswith("_")]
    for attr in public_attrs:
        value = getattr(backend, attr, None)
        assert fake_key not in str(value)


# ----- hallucination-trap regression suite ---------------------------------------------------


def test_hallucination_traps_fixture_loadable() -> None:
    """Sanity check the fixture file is well-formed."""
    with open(FIXTURES / "hallucination_traps.json") as f:
        traps = json.load(f)
    assert len(traps) == 10
    for trap in traps:
        assert "trap_id" in trap
        assert "expected_confidence" in trap
        assert "company" in trap


def test_traps_all_low_confidence_when_backend_returns_nothing() -> None:
    """Hardened guard: if the LLM returns no analysis, every trap must drop to low.

    This is the contract we own. Whether a real LLM correctly classifies these
    trap descriptions is a separate (and harder) test that requires a live model.
    """
    backend = MockBackend({})  # no fixtures = empty response for everyone
    with open(FIXTURES / "hallucination_traps.json") as f:
        traps = json.load(f)
    for trap in traps:
        company = RawCompany.model_validate(trap["company"])
        analysis, _ = asyncio.run(analyze(company, backend))
        assert analysis.confidence == "low", (
            f"trap {trap['trap_id']} ({trap['trap_kind']}) should land at low confidence "
            f"when the backend returns nothing"
        )


def test_traps_with_hallucinated_source_dropped_to_low() -> None:
    """Even if the LLM 'classifies confidently', a fabricated source URL drops it to low."""
    fixture: dict[str, dict[str, str]] = {}
    with open(FIXTURES / "hallucination_traps.json") as f:
        traps = json.load(f)
    for trap in traps:
        slug = trap["company"]["slug"]
        bad_payload = {
            "slug": slug,
            "industry_primary": "B2B SaaS",
            "industry_secondary": [],
            "ai_capability": ["agents"],
            "tech_stack": [],
            "oss_posture": "api-only",
            "oss_evidence_url": None,
            "tagline_rewrite": "should-be-rejected",
            "confidence": "high",  # the LLM is overconfident
            "sources": [
                "https://example.com/fabricated"  # but the source isn't from the inputs
            ],
            "rationale": "The model invented a URL.",
        }
        fixture[slug] = {"1": json.dumps(bad_payload)}

    backend = MockBackend(fixture)
    for trap in traps:
        company = RawCompany.model_validate(trap["company"])
        analysis, _ = asyncio.run(analyze(company, backend))
        assert analysis.confidence == "low", f"trap {trap['trap_id']}: hallucinated source must drop to low"


def test_pii_in_company_description_is_redacted_before_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt sent to the backend must not contain PII even if yc-oss does."""
    company = RawCompany.model_validate(
        {
            "slug": "pii-bait",
            "name": "PII Bait",
            "batch": "Winter 2026",
            "website": "https://piibait.example",
            "one_liner": "Hi I am piibait@example.com",
            "long_description": "Reach me at hello@piibait.example or 555-867-5309 anytime.",
            "industry": "B2B",
            "industries": ["B2B"],
            "url": "https://www.ycombinator.com/companies/pii-bait",
        }
    )
    seen_prompts: list[str] = []

    class CapturingBackend(MockBackend):
        async def complete(self, prompt: str, *, model: str = "x") -> str:
            seen_prompts.append(prompt)
            return ""

    backend = CapturingBackend({})
    asyncio.run(analyze(company, backend))
    assert seen_prompts, "backend was never called"
    sent = seen_prompts[0]
    assert "hello@piibait.example" not in sent
    assert "555-867-5309" not in sent
    assert "[REDACTED_EMAIL]" in sent or "[REDACTED_PHONE]" in sent


def test_environ_api_key_creates_api_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # pragma: allowlist secret
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-from-env")
    backend = make_default_backend()
    assert isinstance(backend, AnthropicAPIBackend)


# ----- lenient parsing (PR #4) ---------------------------------------------------------------


def test_lenient_parser_drops_unknown_capability_keeps_known() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["ai_capability"] = ["agents", "synthetic-data", "unicorn-mode"]
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is not None
    cap_values = [c.value for c in out.ai_capability]
    assert "agents" in cap_values
    assert "synthetic-data" not in cap_values
    assert "unicorn-mode" not in cap_values


def test_lenient_parser_falls_back_to_unclear_when_all_capabilities_unknown() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["ai_capability"] = ["unicorn-mode", "magic-mode"]
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is not None
    assert [c.value for c in out.ai_capability] == ["unclear"]


def test_lenient_parser_drops_unknown_tech_stack_entries() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["tech_stack"] = ["anthropic", "made-up-stack", "openai", "future-model"]
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is not None
    stack_values = [s.value for s in out.tech_stack]
    assert "anthropic" in stack_values
    assert "openai" in stack_values
    assert "made-up-stack" not in stack_values


def test_lenient_parser_keeps_industry_primary_strict() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["industry_primary"] = "Made-Up Vertical"
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is None


def test_lenient_parser_keeps_oss_posture_strict() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["oss_posture"] = "kinda-open-i-guess"
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is None


def test_lenient_parser_truncates_overlong_rationale() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["rationale"] = "x" * 800
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is not None
    assert len(out.rationale) <= 400
    assert out.rationale.endswith("…")  # ellipsis marker


def test_lenient_parser_truncates_overlong_tagline() -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["tagline_rewrite"] = "y" * 200
    out = _parse_response(json.dumps(payload), slug="acme-ai")
    assert out is not None
    assert len(out.tagline_rewrite) <= 140
    assert out.tagline_rewrite.endswith("…")


# ----- raw-failure capture (PR #4) -----------------------------------------------------------


def test_raw_failure_log_captures_schema_failures(tmp_path: Path) -> None:
    backend = MockBackend({})
    log_path = tmp_path / "raw_failures.jsonl"
    company = _make_company()
    asyncio.run(analyze(company, backend, raw_failure_log=log_path))
    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["slug"] == "acme-ai"
    assert record["reason"] == "schema-validation-failure"
    assert "ts" in record


def test_raw_failure_log_captures_hallucinated_url(tmp_path: Path) -> None:
    payload = json.loads(_good_response("acme-ai"))
    payload["sources"] = ["https://invented.example/research"]
    backend = MockBackend({"acme-ai": {"1": json.dumps(payload)}})
    log_path = tmp_path / "raw_failures.jsonl"
    asyncio.run(analyze(_make_company(), backend, raw_failure_log=log_path))
    assert log_path.exists()
    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["reason"] == "hallucinated-source-url"


def test_raw_failure_log_silent_when_no_path() -> None:
    """No raw_failure_log argument → no error even on failure."""
    backend = MockBackend({})
    # Should not raise.
    asyncio.run(analyze(_make_company(), backend))


def test_raw_failure_log_appends_across_calls(tmp_path: Path) -> None:
    backend = MockBackend({})
    log_path = tmp_path / "raw_failures.jsonl"
    for slug in ("a", "b", "c"):
        asyncio.run(
            analyze(
                _make_company(slug=slug, website=f"https://{slug}.example"),
                backend,
                raw_failure_log=log_path,
            )
        )
    assert len(log_path.read_text().splitlines()) == 3
