"""Smoke tests — package import and CLI subcommand invocation."""

from __future__ import annotations

from typer.testing import CliRunner

import ycai
from ycai.cli import app


def test_package_imports() -> None:
    assert ycai.__version__ == "0.1.0"


def test_cli_version_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "yc-ai-pulse" in result.stdout


def test_cli_help_subcommand() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run-coverage" in result.stdout
