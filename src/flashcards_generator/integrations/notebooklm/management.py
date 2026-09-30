from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from flashcards_generator.integrations.notebooklm.gateway import (
    NotebookLMAdapter,
)
from flashcards_generator.integrations.process_capture import (
    close_process_pipes,
    communicate_bounded,
)
from flashcards_generator.services.contracts import CancellationToken
from flashcards_generator.services.dto.workflow import (
    AuthStatus,
    CleanupOutcome,
)

AdapterFactory = Callable[[int], NotebookLMAdapter]


class NotebookLMManagement:
    """NotebookLM process and cleanup operations without presentation logic."""

    def __init__(
        self,
        executable: str,
        adapter_factory: AdapterFactory,
        *,
        notebooklm_profile: str | None = None,
        notebooklm_home: Path | None = None,
        cleanup_show_progress: bool = False,
    ) -> None:
        self._executable = executable
        self._adapter_factory = adapter_factory
        self._notebooklm_profile = notebooklm_profile
        self._notebooklm_home = notebooklm_home
        self._cleanup_show_progress = cleanup_show_progress
        self._active_lock = Lock()
        self._active_token: CancellationToken | None = None

    def auth_status(self) -> AuthStatus:
        """Check authentication through the NotebookLM executable."""
        with self._operation() as token:
            return self._auth_status(token)

    def _auth_status(self, token: CancellationToken) -> AuthStatus:
        result = self._run(["auth", "check"], timeout=10)
        token.raise_if_cancelled()
        if result is None:
            return AuthStatus(False, "unable to check authentication")
        if result.returncode == 0:
            return AuthStatus(True, "authenticated")
        return AuthStatus(
            False, self._failure_message(result, "login required")
        )

    def login(self) -> AuthStatus:
        """Run the NotebookLM login command and return the resulting status."""
        with self._operation() as token:
            result = self._run(["login"], timeout=300)
            if result is None:
                if token.is_cancelled:
                    return AuthStatus(False, "login cancelled")
                return AuthStatus(False, "unable to start login")
            if result.returncode != 0:
                return AuthStatus(
                    False, self._failure_message(result, "login failed")
                )
            if token.is_cancelled:
                return AuthStatus(False, "login cancelled")
            return AuthStatus(True, "authenticated")

    def set_language(self, language: str) -> bool:
        """Set the NotebookLM language, returning command success."""
        if not language.strip():
            raise ValueError("language must not be empty")
        with self._operation():
            result = self._run(["language", "set", language], timeout=60)
            return result is not None and result.returncode == 0

    def cleanup(
        self, *, days: int | None, check_auth: bool = False
    ) -> CleanupOutcome:
        """Delete notebooks without rendering adapter-owned terminal progress."""
        with self._operation() as token:
            if check_auth and not self._auth_status(token).authenticated:
                raise PermissionError("NotebookLM authentication is required")
            token.raise_if_cancelled()
            adapter = self._adapter_factory(900)
            unregister = token.register(adapter.cancel_active)
            try:
                with adapter.cancellation_scope(token):
                    token.raise_if_cancelled()
                    if days is None:
                        deleted, failed = adapter.delete_all_notebooks(
                            show_progress=self._cleanup_show_progress
                        )
                    else:
                        deleted, failed = adapter.delete_all_notebooks(
                            days=days,
                            show_progress=self._cleanup_show_progress,
                        )
            finally:
                unregister()
            return CleanupOutcome(deleted=deleted, failed=failed)

    def cancel_active(self) -> None:
        """Stop a direct command or adapter command in progress."""
        with self._active_lock:
            token = self._active_token
        if token is not None:
            token.cancel()

    def _run(
        self,
        arguments: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str] | None:
        process: subprocess.Popen[str] | None = None
        unregister: Callable[[], None] = lambda: None
        with self._active_lock:
            token = self._active_token
        if token is None:
            raise RuntimeError("management operation is not active")
        token.raise_if_cancelled()
        try:
            process = subprocess.Popen(
                self._command(arguments),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                env=self._environment(),
            )
            unregister = token.register(lambda: self._stop_process(process))
            stdout, stderr = communicate_bounded(process, timeout=timeout)
        except OSError, subprocess.SubprocessError:
            if process is not None:
                self._stop_process(process)
            return None
        finally:
            self._release_process(process, unregister)
        if token.is_cancelled:
            return None
        return subprocess.CompletedProcess(
            self._command(arguments),
            process.returncode,
            stdout,
            stderr,
        )

    @staticmethod
    def _release_process(
        process: subprocess.Popen[str] | None,
        unregister: Callable[[], None],
    ) -> None:
        try:
            unregister()
        finally:
            if process is not None:
                close_process_pipes(process, sys.exception())

    def _command(self, arguments: list[str]) -> list[str]:
        command = [self._executable]
        if self._notebooklm_profile is not None:
            command.extend(["--profile", self._notebooklm_profile])
        command.extend(arguments)
        return command

    def _environment(self) -> dict[str, str] | None:
        if self._notebooklm_home is None:
            return None
        environment = os.environ.copy()
        environment["NOTEBOOKLM_HOME"] = str(self._notebooklm_home)
        return environment

    @contextmanager
    def _operation(self) -> Iterator[CancellationToken]:
        """Publish one fresh cancellation identity for an operation."""
        token = CancellationToken()
        with self._active_lock:
            if self._active_token is not None:
                raise RuntimeError("management operation already active")
            self._active_token = token
        try:
            yield token
        finally:
            with self._active_lock:
                if self._active_token is token:
                    self._active_token = None

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        """Terminate and reap one process group."""
        if process.poll() is not None:
            process.wait(timeout=5)
            return
        NotebookLMManagement._signal_process(
            process, signal.SIGTERM, process.terminate
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            NotebookLMManagement._signal_process(
                process, signal.SIGKILL, process.kill
            )
            process.wait(timeout=5)

    @staticmethod
    def _signal_process(
        process: subprocess.Popen[str],
        signal_number: int,
        fallback: Callable[[], None],
    ) -> None:
        """Signal a process group and fall back to its leader."""
        try:
            os.killpg(process.pid, signal_number)
        except OSError:
            try:
                fallback()
            except ProcessLookupError:
                return

    @staticmethod
    def _failure_message(
        result: subprocess.CompletedProcess[str], fallback: str
    ) -> str:
        return result.stderr.strip()[:200] or fallback
