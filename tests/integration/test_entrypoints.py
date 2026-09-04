"""Failing-first acceptance tests for primary entrypoint dispatch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_primary(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["TERM"] = "xterm-256color"
    return subprocess.run(
        ["uv", "run", "flashcards", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        input="q\n",
        check=False,
        timeout=10,
    )


def _run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["TERM"] = "xterm-256color"
    return subprocess.run(
        ["uv", "run", "python", "-m", "flashcards_generator", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        input="q\n",
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("runner", [_run_primary, _run_module])
def test_primary_help_exposes_textual_shell(runner) -> None:
    """Given --help is requested, the primary app surface is initialized."""
    completed = runner("--help")

    assert completed.returncode == 0
    output = completed.stdout + completed.stderr
    assert "usage:" not in output
    assert "Traceback" not in output
