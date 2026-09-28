from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
    SourceFailure,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)


class GenerateUseCasePort(Protocol):
    def execute(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> list[Deck]: ...


UseCaseFactory = Callable[[int], GenerateUseCasePort]


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
