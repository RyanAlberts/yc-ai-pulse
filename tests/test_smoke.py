"""Phase 0 smoke test — ensures the package imports and CI has something to run."""

from __future__ import annotations

import ycai


def test_package_imports() -> None:
    assert ycai.__version__ == "0.0.1"


def test_cli_stub_runs(capsys: object) -> None:
    from ycai.cli import app

    app()
    # capsys is provided by pytest; this stub will be replaced by a Typer
    # integration test in Phase 1.
