"""DTOs for framework-neutral application workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)

DEFAULT_ANKI_CONNECT_URL = "http://127.0.0.1:8765"


@dataclass(frozen=True, slots=True)
class AnkiExportOptions:
    """Optional configuration for exporting generated decks to Anki."""

    deck_name: str
    url: str = DEFAULT_ANKI_CONNECT_URL
    api_key: str | None = None


@dataclass(frozen=True, slots=True)
class GenerateWorkflowRequest:
    """Request for the complete generation workflow."""

    generation_request: GenerateFlashcardsRequest
    language: str = "pt_BR"
    check_auth: bool = True
    anki: AnkiExportOptions | None = None


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    """Request to remove all or recently created NotebookLM notebooks."""

    days: int | None = None
    check_auth: bool = True

    def __post_init__(self) -> None:
        if self.days is not None and self.days <= 0:
            raise ValueError("days must be positive")


@dataclass(frozen=True, slots=True)
class AuthStatus:
    """Result of checking NotebookLM authentication."""

    authenticated: bool
    message: str = ""


@dataclass(frozen=True, slots=True)
class MergeDetails:
    """Detailed row counts produced by a CSV merge operation."""

    rows_before: int
    rows_written: int
    duplicates_removed: int


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """Result of merging CSV files."""

    output_path: Path
    rows_written: int
    duplicates_removed: int = 0

    @property
    def rows_before(self) -> int:
        """Return the valid input row count before deduplication."""
        return self.rows_written + self.duplicates_removed


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """Result of deleting selected NotebookLM notebooks."""

    deleted: int
    failed: int

    @property
    def succeeded(self) -> bool:
        """Return whether every selected notebook was deleted."""
        return self.failed == 0
