"""Tests for desktop subprocess result handling."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from flashcards_generator.infrastructure.desktop_actions import (
    copy_text,
    open_path,
)


def test_copy_text_reports_nonzero_clipboard_exit() -> None:
    """Given a failing clipboard command, copy reports failure."""
    result = CompletedProcess(["wl-copy"], 1)
    with (
        patch(
            "flashcards_generator.infrastructure.desktop_actions.shutil.which",
            return_value="wl-copy",
        ),
        patch(
            "flashcards_generator.infrastructure.desktop_actions.subprocess.run",
            return_value=result,
        ),
    ):
        assert copy_text("card") is False


def test_open_path_reports_nonzero_desktop_exit(tmp_path: Path) -> None:
    """Given a failing desktop opener, open reports failure."""
    result = CompletedProcess(["xdg-open", str(tmp_path)], 1)
    with (
        patch(
            "flashcards_generator.infrastructure.desktop_actions.shutil.which",
            return_value="xdg-open",
        ),
        patch(
            "flashcards_generator.infrastructure.desktop_actions.subprocess.run",
            return_value=result,
        ),
    ):
        assert open_path(tmp_path) is False
