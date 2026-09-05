"""UI-independent facade for complete application workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from flashcards_generator.application.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressReporter,
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
    CleanupRequest,
    MergeDetails,
    MergeOutcome,
)
from flashcards_generator.domain.entities import Deck
from flashcards_generator.domain.ports.anki_exporter import AnkiExporterPort


class GenerationWorkflowPort(Protocol):
    """Generate decks while publishing framework-neutral progress."""

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        """Run one generation operation."""
        ...


class NotebookLMManagementPort(Protocol):
    """NotebookLM account and notebook-management operations."""

    def auth_status(self) -> AuthStatus:
        """Return the current authentication status."""
        ...

    def login(self) -> AuthStatus:
        """Run the provider login flow and return its resulting status."""
        ...

    def set_language(self, language: str) -> bool:
        """Set the provider output language."""
        ...

    def cleanup(
        self, *, days: int | None, check_auth: bool = False
    ) -> CleanupOutcome:
        """Authenticate and delete selected notebooks as one operation."""
        ...

    def cancel_active(self) -> None:
        """Stop any provider-management operation currently in progress."""
        ...


AnkiExporterFactory = Callable[[AnkiExportOptions], AnkiExporterPort]
MergeOperation = Callable[[MergeCsvRequest], int | MergeDetails]


class ApplicationWorkflows:
    """Single application facade shared by command-line and graphical UIs."""

    def __init__(
        self,
        generation: GenerationWorkflowPort,
        notebooklm: NotebookLMManagementPort,
        *,
        merge_operation: MergeOperation = CsvMerger.merge_detailed,
        anki_exporter_factory: AnkiExporterFactory | None = None,
    ) -> None:
        self._generation = generation
        self._notebooklm = notebooklm
        self._merge_operation = merge_operation
        self._anki_exporter_factory = anki_exporter_factory

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        """Generate flashcards through the configured application operation."""
        if request.language.strip() and not self.set_language(
            request.language
        ):
            raise RuntimeError("Unable to set NotebookLM output language")
        return self._generation.generate(request, reporter, token)

    def merge(self, request: MergeCsvRequest) -> MergeOutcome:
        """Merge CSV files and return both the path and row count."""
        result = self._merge_operation(request)
        details = (
            MergeDetails(result, result, 0)
            if isinstance(result, int)
            else result
        )
        return MergeOutcome(
            output_path=request.folder_path / request.output_filename,
            rows_written=details.rows_written,
            duplicates_removed=details.duplicates_removed,
        )

    def auth_status(self) -> AuthStatus:
        """Return the current NotebookLM authentication status."""
        return self._notebooklm.auth_status()

    def login(self) -> AuthStatus:
        """Run NotebookLM login and return the resulting status."""
        return self._notebooklm.login()

    def set_language(self, language: str) -> bool:
        """Set the NotebookLM output language."""
        return self._notebooklm.set_language(language)

    def cleanup(
        self,
        request: CleanupRequest,
        *,
        confirmed: bool = False,
    ) -> CleanupOutcome:
        """Delete selected notebooks, requiring confirmation for delete-all."""
        if request.days is None and not confirmed:
            raise ValueError("cleanup-all requires explicit confirmation")
        return self._notebooklm.cleanup(
            days=request.days, check_auth=request.check_auth
        )

    def cleanup_all(self, *, confirmed: bool) -> CleanupOutcome:
        """Delete every notebook only after explicit caller confirmation."""
        return self.cleanup(CleanupRequest(), confirmed=confirmed)

    def cancel_management(self) -> None:
        """Stop a provider-management operation owned by the facade."""
        self._notebooklm.cancel_active()

    def export_to_anki(
        self,
        decks: Sequence[Deck],
        options: AnkiExportOptions,
    ) -> int:
        """Export generated decks through the configured Anki port."""
        if self._anki_exporter_factory is None:
            raise RuntimeError("Anki export is not configured")
        exporter = self._anki_exporter_factory(options)
        return sum(exporter.export(deck) for deck in decks)
