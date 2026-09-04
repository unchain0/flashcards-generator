"""Primary TUI entrypoint contract tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_entrypoint(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["TERM"] = "xterm-256color"
    return subprocess.run(
        [sys.executable, "-m", "flashcards_generator", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        input="q\n",
        check=False,
        timeout=5,
    )


def test_module_entrypoint_starts_textual_shell() -> None:
    completed = _run_entrypoint()

    assert completed.returncode == 0
    output = completed.stdout + completed.stderr
    assert "\x1b[?1049h" in output
    assert "Traceback" not in output


def test_secondary_cli_entrypoint_remains_noninteractive() -> None:
    completed = subprocess.run(
        ["uv", "run", "flashcards-cli", "generate", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0
    assert "usage:" in completed.stdout
