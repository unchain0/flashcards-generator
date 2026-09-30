import signal
import subprocess
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from flashcards_generator.domain_models.exceptions import OperationCancelled
from flashcards_generator.integrations.notebooklm.process_runner import (
    NotebookLMProcessRunner,
)
from flashcards_generator.services.contracts import CancellationToken

pytestmark = pytest.mark.usefixtures("mock_bounded_process_output")


def test_command_token_obeys_cancellation_flags() -> None:
    runner = NotebookLMProcessRunner()
    token = Mock()

    with runner.cancellation_scope(token):
        assert runner._command_token(True, False) is token
        assert runner._command_token(False, True) is token
        assert runner._command_token(False, False) is None


def test_cancellation_scope_restores_nested_token() -> None:
    runner = NotebookLMProcessRunner()
    outer = Mock()
    inner = Mock()

    with runner.cancellation_scope(outer):
        with pytest.raises(RuntimeError), runner.cancellation_scope(inner):
            assert runner._cancellation_token is inner
            raise RuntimeError("scope failed")
        assert runner._cancellation_token is outer

    assert runner._cancellation_token is None


def test_wait_before_retry_uses_sleep_or_cancellation_token() -> None:
    runner = NotebookLMProcessRunner()
    token = Mock()

    with patch(
        "flashcards_generator.integrations.notebooklm.process_runner.time.sleep"
    ) as sleep:
        runner.wait_before_retry(0.5)
        with runner.cancellation_scope(token):
            runner.wait_before_retry(1.5)

    sleep.assert_called_once_with(0.5)
    token.wait_or_cancel.assert_called_once_with(1.5)


def test_cancel_active_stops_only_this_runner() -> None:
    first = NotebookLMProcessRunner()
    second = NotebookLMProcessRunner()
    first_stop = Mock()
    second_stop = Mock()
    first._track_stopper(first_stop)
    second._track_stopper(second_stop)

    first.cancel_active()

    first_stop.assert_called_once_with()
    second_stop.assert_not_called()


def test_process_stopper_is_idempotent() -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock()
    with patch.object(runner, "_stop_process") as stop_process:
        stop = runner._process_stopper(process)

        stop()
        stop()

    stop_process.assert_called_once_with(process)


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_uses_list_argv_environment_and_reaps_callbacks(
    mock_popen: Mock,
) -> None:
    process = MagicMock(returncode=0)
    process.communicate.return_value = ("output", "")
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()
    token = Mock()
    unregister = Mock()
    token.register.return_value = unregister

    with runner.cancellation_scope(token):
        result = runner.run_command(
            ["notebooklm", "list", "--json"],
            environment={"NOTEBOOKLM_HOME": "/private/profile"},
            timeout=11,
        )

    assert result == (0, "output", "")
    mock_popen.assert_called_once_with(
        ["notebooklm", "list", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        shell=False,
        start_new_session=True,
        env={"NOTEBOOKLM_HOME": "/private/profile"},
    )
    process.communicate.assert_called_once_with(timeout=11)
    token.raise_if_cancelled.assert_has_calls([call(), call()])
    unregister.assert_called_once_with()


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
@pytest.mark.parametrize(
    "failure",
    [
        OSError("communication failed"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        RuntimeError("communication failed"),
    ],
)
def test_run_command_cleans_up_when_communication_fails(
    mock_popen: Mock,
    failure: Exception,
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    process.communicate.side_effect = failure
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()
    token = Mock()
    unregister = Mock()
    token.register.return_value = unregister
    stop_process = Mock()

    with (
        patch.object(runner, "_stop_process", stop_process),
        runner.cancellation_scope(token),
        pytest.raises(type(failure)) as error,
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    assert error.value is failure
    stop_process.assert_called_once_with(process)
    unregister.assert_called_once_with()
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()
    assert not runner._active_stoppers


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_cleans_up_when_callback_registration_fails(
    mock_popen: Mock,
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()
    token = Mock()
    failure = RuntimeError("registration failed")
    token.register.side_effect = failure
    stop_process = Mock()

    with (
        patch.object(runner, "_stop_process", stop_process),
        runner.cancellation_scope(token),
        pytest.raises(RuntimeError) as error,
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    assert error.value is failure
    stop_process.assert_called_once_with(process)
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()
    assert not runner._active_stoppers


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_unregister_failure_does_not_mask_communication_error(
    mock_popen: Mock,
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    primary_failure = RuntimeError("communication failed")
    process.communicate.side_effect = primary_failure
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()
    token = Mock()
    token.register.return_value = Mock(
        side_effect=RuntimeError("secret cleanup detail")
    )

    with (
        patch.object(runner, "_stop_process"),
        runner.cancellation_scope(token),
        pytest.raises(RuntimeError) as error,
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    assert error.value is primary_failure
    assert any("RuntimeError" in note for note in error.value.__notes__)
    assert all(
        "secret cleanup detail" not in note for note in error.value.__notes__
    )
    assert not runner._active_stoppers


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_preserves_error_when_process_stop_fails(
    mock_popen: Mock,
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    primary_failure = RuntimeError("communication failed")
    process.communicate.side_effect = primary_failure
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()

    with (
        patch.object(
            runner,
            "_stop_process",
            side_effect=OSError("private cleanup detail"),
        ),
        pytest.raises(RuntimeError) as error,
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    assert error.value is primary_failure
    assert error.value.__notes__ == [
        "NotebookLM cleanup failed while stopping subprocess (OSError)."
    ]
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_continues_closing_pipes_after_close_failure(
    mock_popen: Mock,
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    process.stdout.close.side_effect = OSError("private pipe detail")
    primary_failure = RuntimeError("communication failed")
    process.communicate.side_effect = primary_failure
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()

    with (
        patch.object(runner, "_stop_process"),
        pytest.raises(RuntimeError) as error,
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    assert error.value is primary_failure
    assert error.value.__notes__ == [
        "NotebookLM cleanup failed while closing subprocess pipe (OSError)."
    ]
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_skips_absent_pipes_during_failure_cleanup(
    mock_popen: Mock,
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = None
    process.communicate.side_effect = RuntimeError("communication failed")
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()

    with (
        patch.object(runner, "_stop_process"),
        pytest.raises(RuntimeError),
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    process.stdout.close.assert_called_once_with()


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_untracks_stopper_when_unregister_alone_fails(
    mock_popen: Mock,
) -> None:
    process = MagicMock(returncode=0)
    process.communicate.return_value = ("output", "")
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()
    token = Mock()
    token.register.return_value = Mock(
        side_effect=RuntimeError("unregister failed")
    )

    with (
        runner.cancellation_scope(token),
        pytest.raises(RuntimeError, match="unregister failed"),
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    assert not runner._active_stoppers


def test_process_stopper_can_retry_after_failed_cleanup() -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock()
    with patch.object(
        runner,
        "_stop_process",
        side_effect=[RuntimeError("cleanup failed"), None],
    ) as stop_process:
        stop = runner._process_stopper(process)

        with pytest.raises(RuntimeError, match="cleanup failed"):
            stop()
        stop()

    assert stop_process.call_args_list == [call(process), call(process)]


def test_stop_process_does_not_signal_completed_process() -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock(returncode=0)
    process.poll.return_value = 0

    runner._stop_process(process)

    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    process.wait.assert_not_called()


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_raises_sanitized_failure_only_when_checking(
    mock_popen: Mock,
) -> None:
    process = MagicMock(returncode=7)
    process.communicate.return_value = ("", "secret diagnostic")
    mock_popen.return_value = process
    runner = NotebookLMProcessRunner()

    with pytest.raises(
        RuntimeError,
        match=r"NotebookLM command failed \(status=7, stderr_chars=17\)",
    ):
        runner.run_command(["notebooklm", "bad"], environment=None, timeout=2)

    process.communicate.assert_called_once_with(timeout=2)
    assert runner.run_command(
        ["notebooklm", "bad"],
        check=False,
        environment=None,
        timeout=2,
    ) == (7, "", "secret diagnostic")


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_honors_pre_cancel_cleanup_flags(mock_popen: Mock) -> None:
    runner = NotebookLMProcessRunner()
    token = CancellationToken()
    token.cancel()
    process = MagicMock(returncode=0)
    process.poll.return_value = None
    process.communicate.return_value = ("", "")
    mock_popen.return_value = process

    with (
        patch.object(runner, "_stop_process") as stop_process,
        runner.cancellation_scope(token),
    ):
        with pytest.raises(OperationCancelled):
            runner.run_command(["normal"], environment=None, timeout=1)
        assert runner.run_command(
            ["cleanup"],
            cancellable=False,
            cancel_on_token=True,
            ignore_pre_cancel=True,
            raise_on_cancel=False,
            environment=None,
            timeout=1,
        ) == (0, "", "")

    assert mock_popen.call_count == 1
    process.communicate.assert_called_once_with(timeout=1)
    stop_process.assert_called_once_with(process)


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt(), subprocess.TimeoutExpired("cmd", 1)]
)
@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_run_command_cleans_up_when_interrupted(
    mock_popen: Mock, failure: BaseException
) -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    process.communicate.side_effect = failure
    mock_popen.return_value = process
    token = Mock()
    token.register.return_value = Mock()
    stop = Mock()

    with (
        patch.object(runner, "_stop_process", stop),
        runner.cancellation_scope(token),
        pytest.raises(type(failure)),
    ):
        runner.run_command(["notebooklm", "list"], environment=None, timeout=1)

    stop.assert_called_once_with(process)
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()
    assert not runner._active_stoppers


def test_stop_process_escalates_term_to_kill() -> None:
    runner = NotebookLMProcessRunner(cleanup_timeout=5)
    process = MagicMock(pid=123)
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), None]

    with patch(
        "flashcards_generator.integrations.notebooklm.process_runner.os.killpg"
    ) as killpg:
        runner._stop_process(process)

    assert killpg.call_args_list == [
        call(123, signal.SIGTERM),
        call(123, signal.SIGKILL),
    ]
    assert process.wait.call_args_list == [call(timeout=5), call(timeout=5)]


@pytest.mark.parametrize(
    ("platform", "pid", "signal_number", "failure", "fallback"),
    [
        ("posix", 123, signal.SIGTERM, None, "terminate"),
        ("posix", 123, signal.SIGTERM, ProcessLookupError, "terminate"),
        ("nt", 123, signal.SIGTERM, None, "terminate"),
        ("nt", "unknown", signal.SIGKILL, None, "kill"),
    ],
)
def test_signal_process_uses_group_or_leader_fallback(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    pid: int | str,
    signal_number: int,
    failure: type[Exception] | None,
    fallback: str,
) -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock(pid=pid)
    process.poll.return_value = None
    killpg = Mock(side_effect=failure if failure else None)
    monkeypatch.setattr(
        "flashcards_generator.integrations.notebooklm.process_runner.os.name",
        platform,
    )
    monkeypatch.setattr(
        "flashcards_generator.integrations.notebooklm.process_runner.os.killpg",
        killpg,
    )

    runner._signal_process(process, signal_number)

    if platform == "posix" and failure is None and isinstance(pid, int):
        killpg.assert_called_once_with(pid, signal_number)
        getattr(process, fallback).assert_not_called()
    else:
        getattr(process, fallback).assert_called_once_with()


def test_process_group_other_os_errors_fall_back_to_process() -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock(pid=123)
    process.poll.return_value = None

    with patch(
        "flashcards_generator.integrations.notebooklm.process_runner.os.killpg",
        side_effect=PermissionError,
    ):
        runner._signal_process(process, signal.SIGKILL)

    process.kill.assert_called_once_with()


def test_signal_process_skips_fallback_when_process_exits_during_signal() -> (
    None
):
    runner = NotebookLMProcessRunner()
    process = MagicMock(pid=123)
    process.poll.side_effect = [None, 0]

    with patch(
        "flashcards_generator.integrations.notebooklm.process_runner.os.killpg",
        side_effect=ProcessLookupError,
    ):
        runner._signal_process(process, signal.SIGTERM)

    process.terminate.assert_not_called()


def test_signal_process_does_not_signal_completed_process() -> None:
    runner = NotebookLMProcessRunner()
    process = MagicMock(pid=123)
    process.poll.return_value = 0

    with patch(
        "flashcards_generator.integrations.notebooklm.process_runner.os.killpg"
    ) as killpg:
        runner._signal_process(process, signal.SIGTERM)

    killpg.assert_not_called()
    process.terminate.assert_not_called()
