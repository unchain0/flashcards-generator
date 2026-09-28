"""Tests for desktop subprocess result handling."""

from pathlib import Path
from subprocess import CompletedProcess, SubprocessError
from unittest.mock import call, patch

from flashcards_generator.integrations.desktop_actions import (
    copy_text,
    open_path,
)


def test_copy_text_reports_nonzero_clipboard_exit() -> None:
    """Given a failing clipboard command, copy reports failure."""
    result = CompletedProcess(["wl-copy"], 1)
    with (
        patch(
            "flashcards_generator.integrations.desktop_actions.shutil.which",
            return_value="wl-copy",
        ),
        patch(
            "flashcards_generator.integrations.desktop_actions.subprocess.run",
            return_value=result,
        ),
    ):
        assert copy_text("card") is False


def test_copy_text_uses_xclip_when_wayland_clipboard_is_unavailable() -> None:
    with (
        patch(
            "flashcards_generator.integrations.desktop_actions.shutil.which",
            side_effect=[None, "xclip"],
        ) as which,
        patch(
            "flashcards_generator.integrations.desktop_actions.subprocess.run",
            return_value=CompletedProcess(["xclip"], 0),
        ) as run,
    ):
        assert copy_text("card") is True

    assert which.call_args_list == [call("wl-copy"), call("xclip")]
    run.assert_called_once_with(
        ["xclip", "-selection", "clipboard"],
        input="card",
        text=True,
        check=True,
        timeout=5,
    )


def test_copy_text_reports_missing_clipboard_command() -> None:
    with (
        patch(
            "flashcards_generator.integrations.desktop_actions.shutil.which",
            return_value=None,
        ),
        patch(
            "flashcards_generator.integrations.desktop_actions.subprocess.run"
        ) as run,
    ):
        assert copy_text("card") is False

    run.assert_not_called()


def test_copy_text_reports_subprocess_errors() -> None:
    for error_type in (OSError, SubprocessError):
        with (
            patch(
                "flashcards_generator.integrations.desktop_actions.shutil.which",
                return_value="wl-copy",
            ),
            patch(
                "flashcards_generator.integrations.desktop_actions.subprocess.run",
                side_effect=error_type(),
            ),
        ):
            assert copy_text("card") is False


def test_open_path_reports_nonzero_desktop_exit(tmp_path: Path) -> None:
    """Given a failing desktop opener, open reports failure."""
    result = CompletedProcess(["xdg-open", str(tmp_path)], 1)
    with (
        patch(
            "flashcards_generator.integrations.desktop_actions.shutil.which",
            return_value="xdg-open",
        ),
        patch(
            "flashcards_generator.integrations.desktop_actions.subprocess.run",
            return_value=result,
        ),
    ):
        assert open_path(tmp_path) is False


def test_open_path_uses_default_desktop_handler(tmp_path: Path) -> None:
    result = CompletedProcess(["xdg-open", str(tmp_path)], 0)
    with (
        patch(
            "flashcards_generator.integrations.desktop_actions.shutil.which",
            return_value="xdg-open",
        ),
        patch(
            "flashcards_generator.integrations.desktop_actions.subprocess.run",
            return_value=result,
        ) as run,
    ):
        assert open_path(tmp_path) is True

    run.assert_called_once_with(
        ["xdg-open", str(tmp_path)], check=True, timeout=5
    )


def test_open_path_reports_missing_opener() -> None:
    with (
        patch(
            "flashcards_generator.integrations.desktop_actions.shutil.which",
            return_value=None,
        ),
        patch(
            "flashcards_generator.integrations.desktop_actions.subprocess.run"
        ) as run,
    ):
        assert open_path(Path("missing")) is False

    run.assert_not_called()


def test_open_path_reports_subprocess_errors(tmp_path: Path) -> None:
    for error_type in (OSError, SubprocessError):
        with (
            patch(
                "flashcards_generator.integrations.desktop_actions.shutil.which",
                return_value="xdg-open",
            ),
            patch(
                "flashcards_generator.integrations.desktop_actions.subprocess.run",
                side_effect=error_type(),
            ),
        ):
            assert open_path(tmp_path) is False
