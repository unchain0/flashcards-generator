from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock, patch

import pytest

from flashcards_generator.integrations import process_capture
from flashcards_generator.integrations.notebooklm.management import (
    NotebookLMManagement,
)
from flashcards_generator.integrations.notebooklm.process_runner import (
    NotebookLMProcessRunner,
)
from flashcards_generator.integrations.pptx_converter import PPTXConverter
from flashcards_generator.integrations.process_capture import (
    close_process_pipes,
    communicate_bounded,
)


@contextmanager
def child(
    code: str, *, stdout: int = subprocess.PIPE
) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        close_process_pipes(process)


def test_drains_both_pipes_beyond_the_os_pipe_capacity() -> None:
    code = "import os; os.write(1, b'a' * 200000); os.write(2, b'b' * 200000)"
    with child(code) as process:
        assert communicate_bounded(process, timeout=5) == (
            "a" * 200000,
            "b" * 200000,
        )
        assert process.returncode == 0
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed


def test_decodes_utf8_and_normalizes_newlines_at_the_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_capture, "MAX_JSON_BYTES", 8)
    code = r"import os; os.write(1, b'\xc3'); os.write(1, b'\xa9\r\nx\ry\n')"
    with child(code) as process:
        assert communicate_bounded(process, timeout=5) == (
            "\u00e9\nx\ny\n",
            "",
        )


@pytest.mark.parametrize("descriptor", [1, 2])
def test_rejects_excess_output_on_either_pipe(
    descriptor: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_capture, "MAX_JSON_BYTES", 64)
    with child(
        f"import os; os.write({descriptor}, b'x' * 1000000)"
    ) as process:
        with pytest.raises(
            subprocess.SubprocessError, match="exceeds 64 bytes"
        ):
            communicate_bounded(process, timeout=5)
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed


def test_stdout_and_stderr_share_one_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_capture, "MAX_JSON_BYTES", 10)
    with (
        child(
            "import os; os.write(1, b'a' * 6); os.write(2, b'b' * 5)"
        ) as process,
        pytest.raises(subprocess.SubprocessError, match="exceeds 10 bytes"),
    ):
        communicate_bounded(process, timeout=5)


@pytest.mark.parametrize(
    "code",
    [
        "import time; time.sleep(30)",
        "import os, time; os.close(1); os.close(2); time.sleep(30)",
    ],
)
def test_deadline_covers_silent_output_and_waiting_after_eof(
    code: str,
) -> None:
    with child(code) as process:
        with pytest.raises(subprocess.TimeoutExpired):
            communicate_bounded(process, timeout=0.1)
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed


def test_invalid_utf8_closes_both_pipes() -> None:
    with child(r"import os; os.write(1, b'\xff')") as process:
        with pytest.raises(UnicodeDecodeError):
            communicate_bounded(process, timeout=5)
        assert process.returncode == 0
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed


def test_requires_both_output_pipes() -> None:
    with child("pass", stdout=subprocess.DEVNULL) as process:
        with pytest.raises(ValueError, match="both be captured"):
            communicate_bounded(process, timeout=5)
        assert process.stderr is not None and process.stderr.closed


def test_pipe_cleanup_preserves_the_primary_error_and_closes_the_sibling() -> (
    None
):
    primary = subprocess.SubprocessError("output limit exceeded")
    process = MagicMock()
    process.stdout.close.side_effect = OSError("private detail")

    close_process_pipes(process, primary)

    process.stderr.close.assert_called_once_with()
    assert primary.__notes__ == ["Closing subprocess pipe failed (OSError)."]


def test_pipe_cleanup_raises_its_first_error_after_closing_both_pipes() -> (
    None
):
    failure = OSError("close failed")
    process = MagicMock()
    process.stdout.close.side_effect = failure
    process.stderr.close.side_effect = ValueError("private detail")

    with pytest.raises(OSError) as caught:
        close_process_pipes(process)

    assert caught.value is failure
    process.stderr.close.assert_called_once_with()
    assert failure.__notes__ == [
        "Closing subprocess pipe failed (ValueError)."
    ]


def test_notebooklm_runner_reaps_a_process_that_exceeds_the_output_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_capture, "MAX_JSON_BYTES", 64)
    runner = NotebookLMProcessRunner()
    with (
        patch.object(
            runner, "_stop_process", wraps=runner._stop_process
        ) as stop,
        pytest.raises(subprocess.SubprocessError, match="exceeds 64 bytes"),
    ):
        runner.run_command(
            [sys.executable, "-c", "import os; os.write(1, b'x' * 1000000)"],
            environment=None,
            timeout=5,
        )
    process = stop.call_args.args[0]
    assert process.returncode is not None
    assert process.stdout.closed and process.stderr.closed
    assert not runner._active_stoppers


def test_notebooklm_management_reaps_a_process_with_excess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_capture, "MAX_JSON_BYTES", 64)
    manager = NotebookLMManagement(sys.executable, Mock())
    with (
        patch.object(
            manager, "_stop_process", wraps=manager._stop_process
        ) as stop,
        manager._operation(),
    ):
        assert (
            manager._run(
                ["-c", "import os; os.write(2, b'x' * 1000000)"], timeout=5
            )
            is None
        )
    process = stop.call_args.args[0]
    assert process.returncode is not None
    assert process.stdout.closed and process.stderr.closed


def test_pptx_runner_reaps_a_process_with_excess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_capture, "MAX_JSON_BYTES", 64)
    converter = PPTXConverter.__new__(PPTXConverter)
    with (
        patch.object(
            converter, "_stop_process", wraps=converter._stop_process
        ) as stop,
        pytest.raises(subprocess.SubprocessError, match="exceeds 64 bytes"),
    ):
        converter._run_conversion([
            sys.executable,
            "-c",
            "import os; os.write(2, b'x' * 1000000)",
        ])
    process = stop.call_args.args[0]
    assert process.returncode is not None
    assert process.stdout.closed and process.stderr.closed
