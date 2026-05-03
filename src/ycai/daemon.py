"""Process-level lifecycle for the FastAPI daemon.

Cross-platform-ish: we use a PID file + ``signal.SIGTERM`` for shutdown.
On Windows the start path falls back to a foreground run; the rest of the
extension story is macOS/Linux-first anyway.

The on-disk surface lives at ``~/.ycai/``:
- ``token``  — bearer token, mode 0600
- ``daemon.pid`` — PID of the running uvicorn process
- ``daemon.log`` — stdout/stderr (best-effort)
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ycai.server import (
    DAEMON_DIR,
    DEFAULT_HOST,
    DEFAULT_PORT,
    PID_FILE,
    TOKEN_FILE,
    ensure_token,
    health_ping,
)

LOG_FILE = DAEMON_DIR / "daemon.log"


@dataclass(frozen=True)
class DaemonStatus:
    running: bool
    pid: int | None
    health: dict[str, object] | None
    token_present: bool


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        return None


def _process_alive(pid: int) -> bool:
    """Best-effort check. ``os.kill(pid, 0)`` succeeds iff the process exists
    and we have permission to signal it; raises otherwise.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it (e.g. different user). Treat
        # as 'not ours' rather than 'alive' so a stale PID file from another
        # user doesn't block us from starting.
        return False


def status(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> DaemonStatus:
    """Synchronous status probe. Combines PID-file existence with /healthz."""
    pid = _read_pid()
    running = bool(pid and _process_alive(pid))
    return DaemonStatus(
        running=running,
        pid=pid if running else None,
        health=health_ping(host=host, port=port),
        token_present=TOKEN_FILE.exists(),
    )


def start(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> DaemonStatus:
    """Start the daemon if it isn't already running.

    Detached subprocess running ``python -m ycai.server`` with stdout/stderr
    captured to ``~/.ycai/daemon.log``. We do not double-fork; on modern
    macOS/Linux ``start_new_session=True`` is sufficient to detach from the
    parent terminal.
    """
    DAEMON_DIR.mkdir(mode=0o700, exist_ok=True)
    ensure_token()

    current = status(host=host, port=port)
    if current.running and current.health is not None:
        return current

    if current.pid and not _process_alive(current.pid):
        # Stale PID file from a previous unclean shutdown. Drop it.
        PID_FILE.unlink(missing_ok=True)

    env = os.environ.copy()
    env.setdefault("YCAI_HOST", host)
    env.setdefault("YCAI_PORT", str(port))

    log_handle = LOG_FILE.open("ab")
    # sys.executable is the interpreter currently running this module; the
    # arg list is fixed, no shell, no untrusted input. S603 is a false positive
    # in this very common pattern.
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "ycai.server"],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    PID_FILE.write_text(str(proc.pid))

    # Block briefly until /healthz answers, so the caller's "started" message
    # corresponds to a daemon that can actually receive requests.
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        probe = health_ping(host=host, port=port, timeout=0.5)
        if probe is not None:
            return DaemonStatus(running=True, pid=proc.pid, health=probe, token_present=True)
        time.sleep(0.2)

    # If we got here uvicorn didn't come up. Don't leave a half-started daemon.
    with contextlib.suppress(OSError):
        proc.terminate()
    PID_FILE.unlink(missing_ok=True)
    return DaemonStatus(running=False, pid=None, health=None, token_present=TOKEN_FILE.exists())


def stop(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> DaemonStatus:
    """SIGTERM the daemon and wait for it to exit. Best-effort."""
    pid = _read_pid()
    if not pid:
        return status(host=host, port=port)

    if _process_alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        # Give uvicorn a few seconds to drain.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _process_alive(pid):
            time.sleep(0.2)
        if _process_alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    PID_FILE.unlink(missing_ok=True)
    return status(host=host, port=port)


def token() -> str:
    """Return the bearer token (creates it if missing). Never logged."""
    return ensure_token()


__all__ = [
    "LOG_FILE",
    "DaemonStatus",
    "start",
    "status",
    "stop",
    "token",
]


# Reference Path so import-cleanup tools don't strip it; the dataclass
# annotation above uses ``int | None`` and ``dict | None`` but Path is
# imported for future expansion (e.g. ``Path``-typed cwd argument).
_ = Path
