"""Tests for the FastAPI daemon (Phase 3 PR #18). All in-process via the
ASGI test transport — no real port binding, no real subprocess.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ycai.server import (
    REGISTRY,
    RunRecord,
    RunRegistry,
    RunState,
    create_app,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Ensure each test starts with an empty registry."""
    REGISTRY._records.clear()
    REGISTRY._streams.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> httpx.AsyncClient:
    """ASGI client wired to a token + a sandboxed runs/ directory."""
    monkeypatch.setenv("YCAI_TOKEN", "test-token-do-not-use-in-prod")
    monkeypatch.setenv("YCAI_RUNS_DIR", str(tmp_path))
    transport = httpx.ASGITransport(app=create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _bearer(t: str = "test-token-do-not-use-in-prod") -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


# ----- /healthz ------------------------------------------------------------------


def test_healthz_is_public(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert "version" in body

    asyncio.run(go())


# ----- token auth ---------------------------------------------------------------


def test_v1_endpoints_reject_missing_token(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.get("/v1/info")
            assert resp.status_code == 401

    asyncio.run(go())


def test_v1_endpoints_reject_wrong_token(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.get("/v1/info", headers=_bearer("wrong-token"))
            assert resp.status_code == 401

    asyncio.run(go())


def test_v1_endpoints_accept_correct_token(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.get("/v1/info", headers=_bearer())
            assert resp.status_code == 200
            assert "version" in resp.json()

    asyncio.run(go())


# ----- POST /v1/runs ------------------------------------------------------------


def test_start_run_creates_pending_record(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.post("/v1/runs", headers=_bearer(), json={"batch_slug": "winter-2026"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["state"] == "pending"
            assert body["run_id"]
            # Status endpoint should now find it.
            status_resp = await client.get(f"/v1/runs/{body['run_id']}/status", headers=_bearer())
            assert status_resp.status_code == 200
            assert status_resp.json()["state"] == "pending"

    asyncio.run(go())


def test_start_run_accepts_default_batch(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.post("/v1/runs", headers=_bearer(), json={})
            assert resp.status_code == 200
            assert resp.json()["state"] == "pending"

    asyncio.run(go())


def test_run_status_404_on_unknown_id(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.get("/v1/runs/no-such-run/status", headers=_bearer())
            assert resp.status_code == 404

    asyncio.run(go())


# ----- POST /v1/companies/{slug}/deep-dive --------------------------------------


def test_deep_dive_creates_deep_kind_record(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.post("/v1/companies/acme-ai/deep-dive", headers=_bearer())
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]
            status_resp = await client.get(f"/v1/runs/{run_id}/status", headers=_bearer())
            body = status_resp.json()
            assert body["kind"] == "deep-dive"
            assert body["deep_dive_slug"] == "acme-ai"

    asyncio.run(go())


# ----- /v1/companies (autocomplete source) --------------------------------------


def test_companies_404_when_no_runs(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.get("/v1/companies", headers=_bearer())
            assert resp.status_code == 404

    asyncio.run(go())


def test_companies_lists_from_latest_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The endpoint reads ``raw/yc_companies.json`` from the latest run dir."""
    monkeypatch.setenv("YCAI_TOKEN", "test-token-do-not-use-in-prod")
    monkeypatch.setenv("YCAI_RUNS_DIR", str(tmp_path))
    run_dir = tmp_path / "2026-05-03-185145"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "yc_companies.json").write_text(
        json.dumps(
            [
                {
                    "slug": "acme-ai",
                    "name": "Acme AI",
                    "industry": "B2B",
                    "website": "https://acme.ai",
                    "url": "https://www.ycombinator.com/companies/acme-ai",
                },
                {
                    "slug": "beta-co",
                    "name": "Beta Co",
                    "industry": "Healthcare",
                    "website": "https://beta.example",
                    "url": "https://www.ycombinator.com/companies/beta-co",
                },
            ]
        )
    )

    async def go() -> None:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/v1/companies", headers=_bearer())
            assert resp.status_code == 200
            slugs = [c["slug"] for c in resp.json()]
            assert {"acme-ai", "beta-co"} <= set(slugs)

    asyncio.run(go())


# ----- registry behavior --------------------------------------------------------


def test_registry_subscribers_receive_emitted_events() -> None:
    reg = RunRegistry()
    record = RunRecord(
        run_id="r1",
        kind="batch",
        state=RunState.PENDING,
        batch_slug="auto",
        started_at="2026-05-03T00:00:00+00:00",  # type: ignore[arg-type]
    )
    reg.upsert(record)

    async def go() -> None:
        q = reg.subscribe("r1")
        reg.emit("r1", {"event": "tick", "data": {"completed": 1}})
        msg = await asyncio.wait_for(q.get(), timeout=1.0)
        assert msg["event"] == "tick"

    asyncio.run(go())


def test_registry_unsubscribe_stops_delivery() -> None:
    reg = RunRegistry()

    async def go() -> None:
        q = reg.subscribe("r1")
        reg.unsubscribe("r1", q)
        reg.emit("r1", {"event": "tick", "data": {}})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.1)

    asyncio.run(go())


# ----- CORS ---------------------------------------------------------------------


def test_cors_allows_chrome_extension_origin(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.options(
                "/v1/info",
                headers={
                    "Origin": "chrome-extension://abcdefghijklmnopqrstuv",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
            # Preflight should be 200 with the matching origin echoed.
            assert resp.status_code == 200
            assert "chrome-extension://abcdefghijklmnopqrstuv" in resp.headers.get("access-control-allow-origin", "")

    asyncio.run(go())


def test_cors_rejects_non_extension_origin(client: httpx.AsyncClient) -> None:
    async def go() -> None:
        async with client:
            resp = await client.options(
                "/v1/info",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # Without a matching CORS allow header, the browser would refuse
            # the actual request; FastAPI/Starlette returns 400 here.
            assert resp.headers.get("access-control-allow-origin") != "https://evil.example"

    asyncio.run(go())
