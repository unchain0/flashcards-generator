from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer
from typing import Final, Literal

from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressStage,
    ProgressState,
    SourceFailure,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TerminalJobStatus = Literal["completed", "failed", "cancelled"]

_PROGRESS_MESSAGES: Final[dict[ProgressStage, str]] = {
    ProgressStage.DISCOVERY: "Analisando os documentos...",
    ProgressStage.SOURCE: "Processando os documentos...",
    ProgressStage.CHUNK: "Processando uma parte do documento...",
    ProgressStage.GENERATION: "Gerando flashcards no NotebookLM...",
    ProgressStage.DOWNLOAD: "Preparando os arquivos CSV...",
    ProgressStage.CLEANUP: "Finalizando a geração...",
    ProgressStage.AUTH: "Geração em andamento...",
    ProgressStage.EXPORT: "Geração em andamento...",
    ProgressStage.MERGE: "Geração em andamento...",
}
_SOURCE_DELTAS: Final[dict[ProgressState, tuple[int, int, int]]] = {
    ProgressState.STARTED: (0, 0, 0),
    ProgressState.ADVANCED: (0, 0, 0),
    ProgressState.RETRYING: (0, 0, 0),
    ProgressState.SKIPPED: (0, 1, 0),
    ProgressState.COMPLETED: (1, 0, 0),
    ProgressState.FAILED: (0, 0, 1),
}


@dataclass(frozen=True, slots=True)
class LocalJobReservation:
    id: str
    input_dir: Path


@dataclass(frozen=True, slots=True)
class GenerationJobSnapshot:
    id: str
    status: JobStatus
    message: str
    filenames: tuple[str, ...]
    discovered_sources: int
    completed_sources: int
    skipped_sources: int
    failed_sources: int
    artifacts: tuple[str, ...]
    error: str | None


@dataclass(slots=True)
class GenerationJobState:
    id: str
    user_id: str
    workspace: TemporaryDirectory[str]
    input_dir: Path
    output_dir: Path
    request: GenerateFlashcardsRequest
    filenames: tuple[str, ...]
    token: CancellationToken = field(default_factory=CancellationToken)
    status: JobStatus = "queued"
    message: str = "Aguardando o início da geração."
    discovered_sources: int = 0
    completed_sources: int = 0
    skipped_sources: int = 0
    failed_sources: int = 0
    artifacts: dict[str, Path] = field(default_factory=dict)
    error: str | None = None
    finished_at: float | None = None
    expiry_timer: Timer | None = None

    def record(self, event: ProgressEvent) -> None:
        self.message = _PROGRESS_MESSAGES[event.stage]
        if event.stage is ProgressStage.DISCOVERY:
            self._record_discovery(event)
        elif event.stage is ProgressStage.SOURCE:
            self._record_source(event.state)

    def _record_discovery(self, event: ProgressEvent) -> None:
        if event.state is ProgressState.COMPLETED:
            self.discovered_sources = event.total or event.current or 0

    def _record_source(self, state: ProgressState) -> None:
        completed, skipped, failed = _SOURCE_DELTAS[state]
        self.completed_sources += completed
        self.skipped_sources += skipped
        self.failed_sources += failed

    def finish_outcome(
        self, outcome: GenerationOutcome, finished_at: float
    ) -> None:
        output_dir = self.output_dir.resolve()
        self.artifacts = _collect_artifacts(output_dir, outcome.csv_paths)
        failures = outcome.failed_sources
        self.discovered_sources = outcome.discovered_sources
        self.completed_sources = outcome.completed_sources
        self.skipped_sources = outcome.skipped_sources
        self.failed_sources = len(failures)
        self.finish(
            "failed" if failures else "completed",
            "Geração concluída."
            if not failures
            else f"A geração terminou com {len(failures)} arquivo(s) com falha.",
            finished_at,
            _failure_message(failures),
        )

    def finish(
        self,
        status: TerminalJobStatus,
        message: str,
        finished_at: float,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.message = message
        self.error = error
        self.finished_at = finished_at

    def snapshot(self) -> GenerationJobSnapshot:
        return GenerationJobSnapshot(
            id=self.id,
            status=self.status,
            message=self.message,
            filenames=self.filenames,
            discovered_sources=self.discovered_sources,
            completed_sources=self.completed_sources,
            skipped_sources=self.skipped_sources,
            failed_sources=self.failed_sources,
            artifacts=tuple(sorted(self.artifacts)),
            error=self.error,
        )


def _collect_artifacts(
    output_dir: Path, paths: tuple[Path, ...]
) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for path in paths:
        resolved = path.resolve(strict=True)
        if (
            output_dir not in resolved.parents
            or resolved.suffix.lower() != ".csv"
        ):
            raise ValueError("generation returned an invalid artifact path")
        artifacts[resolved.relative_to(output_dir).as_posix()] = resolved
    return artifacts


def _failure_message(failures: tuple[SourceFailure, ...]) -> str | None:
    message = "; ".join(
        f"{failure.source.name}: {failure.reason}" for failure in failures
    )[:2000]
    return message or None
