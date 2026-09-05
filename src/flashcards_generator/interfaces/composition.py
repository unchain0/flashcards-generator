"""Concrete dependency composition for UI-independent workflows."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from time import monotonic

from flashcards_generator.adapters.anki_connect_adapter import (
    AnkiConnectAdapter,
)
from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.application.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
    SourceFailure,
)
from flashcards_generator.application.csv_merger import CsvMerger
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.application.dto.workflow import (
    AnkiExportOptions,
    AuthStatus,
    CleanupOutcome,
    MergeOutcome,
)
from flashcards_generator.application.use_cases import (
    GenerateFlashcardsUseCase,
)
from flashcards_generator.application.workflows import ApplicationWorkflows
from flashcards_generator.infrastructure.chunk_state_repository import (
    FileSystemChunkStateRepository,
)
from flashcards_generator.infrastructure.paths import find_notebooklm
from flashcards_generator.infrastructure.settings import (
    Settings,
    SettingsRepository,
)

UseCaseFactory = Callable[[int], GenerateFlashcardsUseCase]
AdapterFactory = Callable[[int], NotebookLMAdapter]


class _OutcomeReporter:
    """Forward progress while retaining source-level outcome data."""

    def __init__(self, reporter: ProgressReporter) -> None:
        self._reporter = reporter
        self.discovered = 0
        self.completed = 0
        self.skipped = 0
        self.failures: list[SourceFailure] = []

    def publish(self, event: ProgressEvent) -> None:
        self._reporter.publish(event)
        if event.stage == ProgressStage.DISCOVERY:
            self._record_discovery(event)
        elif event.stage == ProgressStage.SOURCE:
            self._record_source(event)

    def _record_discovery(self, event: ProgressEvent) -> None:
        if event.state == ProgressState.COMPLETED:
            self.discovered = event.total or event.current or 0

    def _record_source(self, event: ProgressEvent) -> None:
        if event.state == ProgressState.COMPLETED:
            self.completed += 1
        elif event.state == ProgressState.SKIPPED:
            self.skipped += 1
        elif event.state == ProgressState.FAILED:
            self.failures.append(
                SourceFailure(
                    source=event.source or Path("<unknown>"),
                    reason=event.message,
                )
            )


class UseCaseGenerationWorkflow:
    """Adapt the existing generation use case to the workflow contract."""

    def __init__(self, use_case_factory: UseCaseFactory) -> None:
        self._use_case_factory = use_case_factory

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        """Run generation and derive its outcome from structured events."""
        token.raise_if_cancelled()
        started_at = monotonic()
        outcome_reporter = _OutcomeReporter(reporter)
        previous_csvs = self._csv_snapshot(request.output_dir)
        use_case = self._use_case_factory(request.timeout)
        decks = use_case.execute(
            request,
            reporter=outcome_reporter,
            token=token,
        )
        token.raise_if_cancelled()
        csv_paths = self._changed_csv_paths(request.output_dir, previous_csvs)
        return GenerationOutcome(
            decks=tuple(decks),
            discovered_sources=outcome_reporter.discovered,
            completed_sources=outcome_reporter.completed,
            skipped_sources=outcome_reporter.skipped,
            failed_sources=tuple(outcome_reporter.failures),
            csv_paths=csv_paths,
            elapsed_seconds=monotonic() - started_at,
        )

    @staticmethod
    def _csv_snapshot(output_dir: Path) -> dict[Path, tuple[int, int]]:
        """Capture metadata for CSVs already present before a run."""
        if not output_dir.exists():
            return {}
        return {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in output_dir.rglob("*.csv")
            if path.is_file()
        }

    @classmethod
    def _changed_csv_paths(
        cls,
        output_dir: Path,
        previous_csvs: dict[Path, tuple[int, int]],
    ) -> tuple[Path, ...]:
        """Return only CSVs created or changed by the current run."""
        current_csvs = cls._csv_snapshot(output_dir)
        return tuple(
            path
            for path in sorted(current_csvs)
            if previous_csvs.get(path) != current_csvs[path]
        )


class NotebookLMManagement:
    """NotebookLM process and cleanup operations without presentation logic."""

    def __init__(
        self,
        executable: str,
        adapter_factory: AdapterFactory,
        *,
        cleanup_show_progress: bool = False,
    ) -> None:
        self._executable = executable
        self._adapter_factory = adapter_factory
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
            result = self._run(["login"], timeout=None)
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
            result = self._run(["language", "set", language], timeout=10)
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
        timeout: float | None,
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
                [self._executable, *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            unregister = token.register(lambda: self._stop_process(process))
            stdout, stderr = process.communicate(timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            if process is not None:
                self._stop_process(process)
            return None
        finally:
            unregister()
        if token.is_cancelled:
            return None
        return subprocess.CompletedProcess(
            [self._executable, *arguments],
            process.returncode,
            stdout,
            stderr,
        )

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
            process.wait()
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
            process.wait()

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


class ApplicationServices:
    """Expose workflows and persisted settings to interface adapters."""

    def __init__(
        self,
        workflows: ApplicationWorkflows,
        settings: SettingsRepository,
    ) -> None:
        self._workflows = workflows
        self._settings = settings

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        """Run generation through the shared facade."""
        return self._workflows.generate(request, reporter, token)

    def merge(self, request: MergeCsvRequest) -> MergeOutcome:
        """Run CSV merge through the shared facade."""
        return self._workflows.merge(request)

    def auth_status(self) -> AuthStatus:
        """Return the current NotebookLM authentication state."""
        return self._workflows.auth_status()

    def login(self) -> AuthStatus:
        """Delegate NotebookLM login."""
        return self._workflows.login()

    def cleanup_all(self, *, confirmed: bool) -> CleanupOutcome:
        """Delegate confirmed deletion of every NotebookLM notebook."""
        return self._workflows.cleanup_all(confirmed=confirmed)

    def cancel_management(self) -> None:
        """Stop an active NotebookLM management operation."""
        self._workflows.cancel_management()

    def set_language(self, language: str) -> bool:
        """Delegate the NotebookLM language setting."""
        return self._workflows.set_language(language)

    def load(self) -> Settings:
        """Load persisted TUI defaults."""
        return self._settings.load()

    def save(self, settings: Settings) -> None:
        """Persist TUI defaults."""
        self._settings.save(settings)


def create_workflows(
    *,
    notebooklm_path: str | None = None,
    use_case_factory: UseCaseFactory | None = None,
    adapter_factory: AdapterFactory | None = None,
    merge_operation: Callable[[MergeCsvRequest], int] | None = None,
    cleanup_show_progress: bool = False,
) -> ApplicationWorkflows:
    """Compose the production workflow facade with replaceable factories."""
    executable = notebooklm_path or find_notebooklm()

    def default_adapter_factory(timeout: int) -> NotebookLMAdapter:
        return NotebookLMAdapter(executable, timeout=timeout)

    resolved_adapter_factory = adapter_factory or default_adapter_factory

    def default_use_case_factory(timeout: int) -> GenerateFlashcardsUseCase:
        return GenerateFlashcardsUseCase(
            generator=resolved_adapter_factory(timeout),
            chunk_state_repository=FileSystemChunkStateRepository(),
        )

    generation = UseCaseGenerationWorkflow(
        use_case_factory or default_use_case_factory
    )
    management = NotebookLMManagement(
        executable,
        resolved_adapter_factory,
        cleanup_show_progress=cleanup_show_progress,
    )
    return ApplicationWorkflows(
        generation,
        management,
        merge_operation=merge_operation or CsvMerger.merge_detailed,
        anki_exporter_factory=_create_anki_exporter,
    )


def _create_anki_exporter(options: AnkiExportOptions) -> AnkiConnectAdapter:
    return AnkiConnectAdapter(
        deck_name=options.deck_name,
        url=options.url,
        api_key=options.api_key,
    )


def create_services(
    *,
    notebooklm_path: str | None = None,
    use_case_factory: UseCaseFactory | None = None,
    adapter_factory: AdapterFactory | None = None,
    settings_repository: SettingsRepository | None = None,
) -> ApplicationServices:
    """Compose interface-ready workflows with persistent settings."""
    workflows = create_workflows(
        notebooklm_path=notebooklm_path,
        use_case_factory=use_case_factory,
        adapter_factory=adapter_factory,
    )
    return ApplicationServices(
        workflows,
        settings_repository or SettingsRepository(),
    )
