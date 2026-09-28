from __future__ import annotations

import logging
import secrets
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock, Timer
from time import monotonic
from typing import Final, Protocol

from flashcards_generator.domain_models.exceptions import (
    FlashcardsGeneratorError,
    OperationCancelled,
)
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressReporter,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.dto.local_generation import (
    GenerationOptions,
)
from flashcards_generator.services.local_generation_state import (
    GenerationJobSnapshot,
    GenerationJobState,
    LocalJobReservation,
    TerminalJobStatus,
)

MAX_PENDING_JOBS: Final = 3
MAX_RETAINED_JOBS: Final = 20
JOB_RETENTION_SECONDS: Final = 1800

logger = logging.getLogger("companion.generation")


class LocalJobsClosedError(RuntimeError):
    pass


class PendingJobLimitError(RuntimeError):
    pass


class RetainedJobLimitError(RuntimeError):
    pass


class LocalGenerationWorkflow(Protocol):
    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome: ...


class LocalWorkflowProvider(Protocol):
    def for_user(self, user_id: str) -> LocalGenerationWorkflow: ...


class _JobReporter(ProgressReporter):
    def __init__(
        self,
        jobs: LocalGenerationJobs,
        job: GenerationJobState,
    ) -> None:
        self._jobs = jobs
        self._job = job

    def publish(self, event: ProgressEvent) -> None:
        self._jobs._record_progress(self._job, event)


class LocalGenerationJobs:
    def __init__(self, workflows: LocalWorkflowProvider) -> None:
        self._workflows = workflows
        self._jobs: dict[str, GenerationJobState] = {}
        self._lock = RLock()
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="flashcards-companion"
        )

    def reserve(
        self,
        user_id: str,
        filenames: list[str],
        options: GenerationOptions,
    ) -> LocalJobReservation:
        job = self._new_job(user_id, filenames, options)
        return LocalJobReservation(job.id, job.input_dir)

    def submit(self, job_id: str) -> GenerationJobSnapshot:
        with self._lock:
            if self._closed:
                raise LocalJobsClosedError
            job = self._jobs[job_id]
            self._executor.submit(self._run, job)
            return job.snapshot()

    def discard(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                self._discard(job)

    def get(self, user_id: str, job_id: str) -> GenerationJobSnapshot | None:
        with self._lock:
            self._prune_expired()
            job = self._jobs.get(job_id)
            return (
                job.snapshot()
                if job is not None and job.user_id == user_id
                else None
            )

    def artifact(self, user_id: str, job_id: str, name: str) -> Path | None:
        with self._lock:
            self._prune_expired()
            job = self._jobs.get(job_id)
            if job is None or job.user_id != user_id:
                return None
            path = job.artifacts.get(name)
            return path if path is not None and path.is_file() else None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = tuple(self._jobs.values())
            for job in jobs:
                self._cancel_job(job)
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            for job in jobs:
                self._cancel_expiry_timer(job)
            self._jobs.clear()
        for job in jobs:
            job.workspace.cleanup()

    def _new_job(
        self,
        user_id: str,
        filenames: list[str],
        options: GenerationOptions,
    ) -> GenerationJobState:
        with self._lock:
            self._prune_expired()
            if self._closed:
                raise LocalJobsClosedError
            pending = sum(
                job.status in {"queued", "running"}
                for job in self._jobs.values()
            )
            if pending >= MAX_PENDING_JOBS:
                raise PendingJobLimitError
            self._make_room()
            workspace = TemporaryDirectory(prefix="flashcards-companion-")
            root = Path(workspace.name)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            request = GenerateFlashcardsRequest(
                input_dir=input_dir,
                output_dir=output_dir,
                difficulty=options.difficulty,
                quantity=options.quantity,
                language=options.language,
                instructions=options.instructions,
                timeout=options.timeout,
                resume=True,
                explicit_files=filenames,
            )
            job = GenerationJobState(
                id=secrets.token_hex(16),
                user_id=user_id,
                workspace=workspace,
                input_dir=input_dir,
                output_dir=output_dir,
                request=request,
                filenames=tuple(filenames),
            )
            self._jobs[job.id] = job
            return job

    def _run(self, job: GenerationJobState) -> None:
        if not self._start_job(job):
            self._cleanup_job(job)
            return
        try:
            self._execute_job(job)
        finally:
            error = sys.exception()
            if isinstance(error, Exception) and job.status == "running":
                self._finish_unexpected_error(job, error)
            self._cleanup_job(job)

    def _execute_job(self, job: GenerationJobState) -> None:
        try:
            workflow = self._workflows.for_user(job.user_id)
            outcome = workflow.generate(
                job.request,
                _JobReporter(self, job),
                job.token,
            )
            self._finish_outcome(job, outcome)
        except OperationCancelled:
            with self._lock:
                self._finish(job, "cancelled", "Geração cancelada.")
        except (
            FlashcardsGeneratorError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            self._finish_expected_error(job, error)

    def _start_job(self, job: GenerationJobState) -> bool:
        with self._lock:
            if job.token.is_cancelled:
                self._finish(job, "cancelled", "Geração cancelada.")
                return False
            job.status = "running"
            job.message = "Analisando os documentos..."
        return True

    def _finish_expected_error(
        self, job: GenerationJobState, error: Exception
    ) -> None:
        logger.error(
            "Local generation job failed",
            extra={"job_id": job.id, "error_type": type(error).__name__},
        )
        with self._lock:
            self._finish(
                job,
                "failed",
                "A geração falhou. Confira a conexão do NotebookLM e tente novamente.",
                f"Falha local: {type(error).__name__}.",
            )

    def _finish_unexpected_error(
        self, job: GenerationJobState, error: Exception
    ) -> None:
        logger.exception(
            "Unexpected local generation failure",
            extra={"job_id": job.id, "error_type": type(error).__name__},
        )
        with self._lock:
            self._finish(
                job,
                "failed",
                "A geração falhou por um erro interno local.",
                "Falha interna local.",
            )

    def _cleanup_job(self, job: GenerationJobState) -> None:
        try:
            shutil.rmtree(job.input_dir)
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.error(
                "Could not remove local generation inputs",
                extra={"job_id": job.id, "error_type": type(error).__name__},
            )
        with self._lock:
            if not job.artifacts:
                job.workspace.cleanup()

    def _finish_outcome(
        self, job: GenerationJobState, outcome: GenerationOutcome
    ) -> None:
        with self._lock:
            job.finish_outcome(outcome, monotonic())
            self._schedule_expiry(job)

    def _record_progress(
        self, job: GenerationJobState, event: ProgressEvent
    ) -> None:
        with self._lock:
            job.record(event)

    def _finish(
        self,
        job: GenerationJobState,
        status: TerminalJobStatus,
        message: str,
        error: str | None = None,
    ) -> None:
        job.finish(status, message, monotonic(), error)
        self._schedule_expiry(job)

    def _schedule_expiry(self, job: GenerationJobState) -> None:
        job.expiry_timer = Timer(
            JOB_RETENTION_SECONDS,
            self._expire,
            args=(job.id,),
        )
        job.expiry_timer.daemon = True
        job.expiry_timer.start()

    def _prune_expired(self) -> None:
        now = monotonic()
        expired = [
            job
            for job in self._jobs.values()
            if job.finished_at is not None
            and now - job.finished_at > JOB_RETENTION_SECONDS
        ]
        for job in expired:
            self._discard(job)

    def _make_room(self) -> None:
        if len(self._jobs) < MAX_RETAINED_JOBS:
            return
        oldest = self._oldest_finished_job()
        if oldest is None:
            raise RetainedJobLimitError
        self._discard(oldest)

    def _oldest_finished_job(self) -> GenerationJobState | None:
        finished_jobs = [
            job for job in self._jobs.values() if job.finished_at is not None
        ]
        if not finished_jobs:
            return None
        return min(
            finished_jobs,
            key=lambda job: job.finished_at or 0,
        )

    def _expire(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if (
                job is not None
                and job.finished_at is not None
                and monotonic() - job.finished_at >= JOB_RETENTION_SECONDS
            ):
                self._discard(job)

    def _discard(self, job: GenerationJobState) -> None:
        self._jobs.pop(job.id, None)
        job.token.cancel()
        self._cancel_expiry_timer(job)
        job.workspace.cleanup()

    def _cancel_job(self, job: GenerationJobState) -> None:
        job.token.cancel()
        self._cancel_expiry_timer(job)

    @staticmethod
    def _cancel_expiry_timer(job: GenerationJobState) -> None:
        if job.expiry_timer is not None:
            job.expiry_timer.cancel()
