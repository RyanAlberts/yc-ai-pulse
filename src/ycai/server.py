"""Local FastAPI daemon — the surface the Chrome extension talks to.

Per ADR 0002: bound to ``127.0.0.1`` only, CORS allowlist limited to
``chrome-extension://*``, every authenticated endpoint requires a bearer
token issued at first daemon start (stored at ``~/.ycai/token``).

The daemon is intentionally thin: it owns the HTTP surface and run
bookkeeping; the heavy lifting (scrape, enrich, render) lives in the
existing pipeline modules. State is in-memory; the run directories on
disk are the source of truth for completed work.

Endpoint surface (v1):
- ``GET  /healthz`` — public, alive check
- ``GET  /v1/info`` — version + latest run summary
- ``GET  /v1/companies`` — list latest-batch companies (for extension autocomplete)
- ``POST /v1/runs`` — kick off a fresh enrichment run; returns run_id
- ``GET  /v1/runs`` — list known runs (most recent first)
- ``GET  /v1/runs/{run_id}/status`` — current state + counts
- ``GET  /v1/runs/{run_id}/events`` — SSE stream of progress
- ``POST /v1/companies/{slug}/deep-dive`` — single-company enrichment (returns run_id)

All v1 endpoints require ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ycai import __version__

log = logging.getLogger(__name__)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DAEMON_DIR = Path.home() / ".ycai"
TOKEN_FILE = DAEMON_DIR / "token"
PID_FILE = DAEMON_DIR / "daemon.pid"
RUNS_DIR_ENV = "YCAI_RUNS_DIR"
RUNS_DIR_DEFAULT = Path("runs")


def runs_dir() -> Path:
    """Where finished pipeline outputs live. Configurable for tests."""
    override = os.environ.get(RUNS_DIR_ENV)
    return Path(override) if override else RUNS_DIR_DEFAULT


# ----- token management ----------------------------------------------------------


def ensure_token() -> str:
    """Generate (or read) the daemon's bearer token.

    The token is stored at ``~/.ycai/token`` with mode 0600. The Chrome
    extension reads it from the user's clipboard at first paste; we never
    transmit it on the network ourselves and never log its value.
    """
    DAEMON_DIR.mkdir(mode=0o700, exist_ok=True)
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)
    return token


def _verify_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency. Raises 401 unless Authorization carries the right token.

    A test can stub the token via the ``YCAI_TOKEN`` environment variable,
    which takes priority over the on-disk file.
    """
    expected = os.environ.get("YCAI_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else None)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="daemon token not configured (run `ycai daemon start` first)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    presented = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


# ----- in-memory run registry ----------------------------------------------------


class RunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunRecord(BaseModel):
    run_id: str  # the timestamp dir name, e.g. "2026-05-03-185145"
    kind: str  # "batch" | "deep-dive"
    state: RunState
    batch_slug: str
    started_at: datetime
    completed_at: datetime | None = None
    completed: int = 0
    total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    error: str | None = None
    deep_dive_slug: str | None = None  # only for kind=deep-dive


class RunRegistry:
    """Tracks active and recent runs in memory.

    Completed runs are persisted on disk (the run directory itself); this
    registry just keeps a fast index for ``GET /v1/runs`` and the SSE stream.
    """

    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        # Per-run async event queues for SSE subscribers.
        self._streams: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def upsert(self, record: RunRecord) -> None:
        self._records[record.run_id] = record

    def get(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def list_recent(self, limit: int = 20) -> list[RunRecord]:
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)[:limit]

    def emit(self, run_id: str, event: dict[str, Any]) -> None:
        for q in self._streams.get(run_id, []):
            q.put_nowait(event)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._streams.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        if run_id in self._streams and q in self._streams[run_id]:
            self._streams[run_id].remove(q)


REGISTRY = RunRegistry()


# ----- request/response bodies ---------------------------------------------------


class StartRunBody(BaseModel):
    batch_slug: str | None = None  # default: latest


class StartRunResponse(BaseModel):
    run_id: str
    state: RunState


class InfoResponse(BaseModel):
    version: str
    runs_dir: str
    runs_count: int
    latest_run_id: str | None


class CompanyEntry(BaseModel):
    slug: str
    name: str
    industry: str
    website: str
    yc_url: str


# ----- helpers -------------------------------------------------------------------


def _list_completed_runs() -> list[Path]:
    if not runs_dir().exists():
        return []
    return sorted((p for p in runs_dir().iterdir() if p.is_dir()), reverse=True)


def _latest_run_dir() -> Path | None:
    runs = _list_completed_runs()
    return runs[0] if runs else None


def _load_companies(run_dir: Path) -> list[CompanyEntry]:
    raw = run_dir / "raw" / "yc_companies.json"
    if not raw.exists():
        return []
    data = json.loads(raw.read_text())
    out: list[CompanyEntry] = []
    for c in data:
        out.append(
            CompanyEntry(
                slug=c.get("slug", ""),
                name=c.get("name", ""),
                industry=c.get("industry", ""),
                website=c.get("website", ""),
                yc_url=c.get("url", ""),
            )
        )
    return out


# ----- the FastAPI app -----------------------------------------------------------


def create_app() -> FastAPI:
    """Factory so tests can build their own instance without touching the global."""
    app = FastAPI(title="yc-ai-pulse daemon", version=__version__)

    # CORS: only Chrome extensions and our own origin can call us. Per ADR 0002
    # the daemon also binds to 127.0.0.1, so even with permissive CORS only
    # local browsers can reach it.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^chrome-extension://[a-z]+$",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "now": datetime.now(UTC).isoformat(),
        }

    @app.get("/v1/info", response_model=InfoResponse, dependencies=[Depends(_verify_token)])
    async def info() -> InfoResponse:
        runs = _list_completed_runs()
        return InfoResponse(
            version=__version__,
            runs_dir=str(runs_dir().resolve()),
            runs_count=len(runs),
            latest_run_id=runs[0].name if runs else None,
        )

    @app.get("/v1/companies", dependencies=[Depends(_verify_token)])
    async def list_companies(run_id: str | None = None) -> list[CompanyEntry]:
        target = runs_dir() / run_id if run_id else _latest_run_dir()
        if target is None or not target.exists():
            raise HTTPException(status_code=404, detail="no runs available")
        return _load_companies(target)

    @app.get("/v1/runs", dependencies=[Depends(_verify_token)])
    async def list_runs() -> list[RunRecord]:
        return REGISTRY.list_recent(limit=20)

    @app.get("/v1/runs/{run_id}/status", dependencies=[Depends(_verify_token)])
    async def run_status(run_id: str) -> RunRecord:
        record = REGISTRY.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return record

    @app.post("/v1/runs", response_model=StartRunResponse, dependencies=[Depends(_verify_token)])
    async def start_run(body: StartRunBody) -> StartRunResponse:
        # The actual pipeline kickoff is wired in PR #19 once the extension
        # has a way to drive it. For PR #18 we expose the contract and a
        # stub that returns a pending run record so the SSE / status
        # endpoints can be wired and tested end-to-end.
        run_id = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
        record = RunRecord(
            run_id=run_id,
            kind="batch",
            state=RunState.PENDING,
            batch_slug=body.batch_slug or "auto",
            started_at=datetime.now(UTC),
        )
        REGISTRY.upsert(record)
        REGISTRY.emit(run_id, {"event": "started", "data": record.model_dump(mode="json")})
        return StartRunResponse(run_id=run_id, state=record.state)

    @app.post("/v1/companies/{slug}/deep-dive", response_model=StartRunResponse, dependencies=[Depends(_verify_token)])
    async def start_deep_dive(slug: str) -> StartRunResponse:
        run_id = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-deep")
        record = RunRecord(
            run_id=run_id,
            kind="deep-dive",
            state=RunState.PENDING,
            batch_slug="auto",
            deep_dive_slug=slug,
            started_at=datetime.now(UTC),
        )
        REGISTRY.upsert(record)
        REGISTRY.emit(run_id, {"event": "started", "data": record.model_dump(mode="json")})
        return StartRunResponse(run_id=run_id, state=record.state)

    @app.get("/v1/runs/{run_id}/events", dependencies=[Depends(_verify_token)])
    async def run_events(run_id: str) -> EventSourceResponse:
        record = REGISTRY.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")

        async def stream() -> AsyncIterator[dict[str, Any]]:
            q = REGISTRY.subscribe(run_id)
            try:
                # First, replay current state so a late subscriber sees something.
                current = REGISTRY.get(run_id)
                if current is not None:
                    yield {"event": "state", "data": json.dumps(current.model_dump(mode="json"))}
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield {"event": msg.get("event", "message"), "data": json.dumps(msg.get("data", {}))}
                        if msg.get("event") in {"completed", "failed"}:
                            break
                    except TimeoutError:
                        # heartbeat to keep the connection alive
                        yield {"event": "heartbeat", "data": "{}"}
            finally:
                REGISTRY.unsubscribe(run_id, q)

        return EventSourceResponse(stream())

    return app


# ----- daemon-side health ping (used by `ycai daemon status`) --------------------


def health_ping(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 1.5) -> dict[str, Any] | None:
    """Synchronous probe of /healthz from CLI. Returns parsed JSON or None."""
    url = f"http://{host}:{port}/healthz"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
    except (httpx.HTTPError, OSError):
        return None


# ----- entrypoint for `python -m ycai.server` (handy for dev) --------------------


def main() -> None:  # pragma: no cover  CLI helper
    import uvicorn

    ensure_token()
    uvicorn.run(
        create_app(),
        host=os.environ.get("YCAI_HOST", DEFAULT_HOST),
        port=int(os.environ.get("YCAI_PORT", str(DEFAULT_PORT))),
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "PID_FILE",
    "REGISTRY",
    "TOKEN_FILE",
    "RunRecord",
    "RunRegistry",
    "RunState",
    "create_app",
    "ensure_token",
    "health_ping",
]
