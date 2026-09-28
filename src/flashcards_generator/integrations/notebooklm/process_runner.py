from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock

from flashcards_generator.services.ports.cancellation import CancellationPort


class NotebookLMProcessRunner:
    def __init__(self, cleanup_timeout: int = 5) -> None:
        self._cleanup_timeout = cleanup_timeout
        self._cancellation_token: CancellationPort | None = None
        self._active_stoppers: set[Callable[[], None]] = set()
        self._active_stoppers_lock = Lock()

    def cancel_active(self) -> None:
        with self._active_stoppers_lock:
            stoppers = tuple(self._active_stoppers)
        for stop_process in stoppers:
            stop_process()

    @contextmanager
    def cancellation_scope(
        self, token: CancellationPort | None
    ) -> Iterator[None]:
        previous_token = self._cancellation_token
        self._cancellation_token = token
        try:
            yield
        finally:
            self._cancellation_token = previous_token

    def wait_before_retry(self, timeout: float) -> None:
        if self._cancellation_token is None:
            time.sleep(timeout)
            return
        self._cancellation_token.wait_or_cancel(timeout)

    def run_command(
        self,
        command: list[str],
        *,
        environment: dict[str, str] | None,
        timeout: int,
        check: bool = True,
        cancellable: bool = True,
        cancel_on_token: bool = False,
        ignore_pre_cancel: bool = False,
        raise_on_cancel: bool = True,
    ) -> tuple[int, str, str]:
        token = self._command_token(cancellable, cancel_on_token)
        self._check_before_command(token, ignore_pre_cancel)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=False,
            start_new_session=True,
            env=environment,
        )
        stop_process = self._process_stopper(process)
        self._track_stopper(stop_process)
        unregister: Callable[[], None] | None = None
        primary_error: BaseException | None = None
        try:
            unregister = self._register_process_stopper(token, stop_process)
            stdout, stderr = self._communicate(process, timeout)
        except (Exception, KeyboardInterrupt) as error:
            primary_error = error
            self._cleanup_after_failure(process, stop_process, error)
            raise
        finally:
            self._finalize_stopper(unregister, stop_process, primary_error)

        self._check_after_command(token, raise_on_cancel)
        if check and process.returncode != 0:
            raise RuntimeError(
                self.command_failure(process.returncode, stderr)
            )
        return process.returncode, stdout, stderr

    def _command_token(
        self, cancellable: bool, cancel_on_token: bool
    ) -> CancellationPort | None:
        if cancellable or cancel_on_token:
            return self._cancellation_token
        return None

    @staticmethod
    def _check_before_command(
        token: CancellationPort | None, ignore_pre_cancel: bool
    ) -> None:
        if not ignore_pre_cancel:
            NotebookLMProcessRunner._raise_if_cancelled(token)

    @staticmethod
    def _check_after_command(
        token: CancellationPort | None, raise_on_cancel: bool
    ) -> None:
        if raise_on_cancel:
            NotebookLMProcessRunner._raise_if_cancelled(token)

    @staticmethod
    def _raise_if_cancelled(token: CancellationPort | None) -> None:
        if token is not None:
            token.raise_if_cancelled()

    def _process_stopper(
        self, process: subprocess.Popen[str]
    ) -> Callable[[], None]:
        stopped = False
        stop_lock = Lock()

        def stop_process() -> None:
            nonlocal stopped
            with stop_lock:
                if stopped:
                    return
                self._stop_process(process)
                stopped = True

        return stop_process

    @staticmethod
    def _register_process_stopper(
        token: CancellationPort | None, stop_process: Callable[[], None]
    ) -> Callable[[], None]:
        if token is None:
            return lambda: None
        return token.register(stop_process)

    @staticmethod
    def _communicate(
        process: subprocess.Popen[str],
        timeout: int,
    ) -> tuple[str, str]:
        return process.communicate(timeout=timeout)

    def _cleanup_after_failure(
        self,
        process: subprocess.Popen[str],
        stop_process: Callable[[], None],
        primary_error: BaseException,
    ) -> None:
        try:
            stop_process()
        except (
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as cleanup_error:
            self._add_cleanup_failure(
                primary_error, "stopping subprocess", cleanup_error
            )

        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError) as cleanup_error:
                    self._add_cleanup_failure(
                        primary_error, "closing subprocess pipe", cleanup_error
                    )

    @staticmethod
    def _add_cleanup_failure(
        primary_error: BaseException,
        operation: str,
        cleanup_error: Exception,
    ) -> None:
        primary_error.add_note(
            "NotebookLM cleanup failed while "
            f"{operation} ({type(cleanup_error).__name__})."
        )

    def _finalize_stopper(
        self,
        unregister: Callable[[], None] | None,
        stop_process: Callable[[], None],
        primary_error: BaseException | None,
    ) -> None:
        try:
            if unregister is not None:
                unregister()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            self._add_cleanup_failure(
                primary_error,
                "unregistering cancellation callback",
                cleanup_error,
            )
        finally:
            self._untrack_stopper(stop_process)

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        self._signal_process(process, signal.SIGTERM)
        try:
            process.wait(timeout=self._cleanup_timeout)
        except subprocess.TimeoutExpired:
            self._signal_process(process, signal.SIGKILL)
            process.wait()

    @staticmethod
    def _signal_process(
        process: subprocess.Popen[str], signal_number: int
    ) -> None:
        if process.poll() is not None:
            return
        if NotebookLMProcessRunner._signal_process_group(
            process, signal_number
        ):
            return
        if signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    @staticmethod
    def _signal_process_group(
        process: subprocess.Popen[str], signal_number: int
    ) -> bool:
        pid = getattr(process, "pid", None)
        if os.name != "posix" or not isinstance(pid, int):
            return False
        try:
            os.killpg(pid, signal_number)
        except OSError:
            return process.poll() is not None
        return True

    @staticmethod
    def command_failure(returncode: int, stderr: str) -> str:
        return (
            "NotebookLM command failed "
            f"(status={returncode}, stderr_chars={len(stderr)})"
        )

    def _track_stopper(self, stop_process: Callable[[], None]) -> None:
        with self._active_stoppers_lock:
            self._active_stoppers.add(stop_process)

    def _untrack_stopper(self, stop_process: Callable[[], None]) -> None:
        with self._active_stoppers_lock:
            self._active_stoppers.discard(stop_process)
