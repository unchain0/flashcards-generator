"""Typed boundaries shared by the Textual workflow panels."""

from __future__ import annotations

from typing import Protocol

from flashcards_generator.application.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressReporter,
)
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.application.dto.workflow import (
    AuthStatus,
    CleanupOutcome,
    MergeOutcome,
)
from flashcards_generator.infrastructure.settings import Settings


class WorkflowServices(Protocol):
    """Application operations consumed by the TUI.

    Composition roots may provide one object implementing every operation or a
    narrower object to the panel that needs it.
    """

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        """Generate cards for an explicit set of selected sources."""
        ...

    def merge(self, request: MergeCsvRequest) -> MergeOutcome:
        """Merge CSV files according to the supplied request."""
        ...

    def auth_status(self) -> AuthStatus:
        """Return the current NotebookLM authentication state."""
        ...

    def login(self) -> AuthStatus:
        """Run the provider login flow and return its resulting state."""
        ...

    def cleanup_all(self, *, confirmed: bool) -> CleanupOutcome:
        """Delete all provider notebooks after explicit confirmation."""
        ...

    def cancel_management(self) -> None:
        """Stop an active NotebookLM management operation."""
        ...

    def set_language(self, language: str) -> bool:
        """Set the provider output language."""
        ...

    def load(self) -> Settings:
        """Load persisted UI settings."""
        ...

    def save(self, settings: Settings) -> None:
        """Persist UI settings."""
        ...
