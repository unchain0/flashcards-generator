"""Port for flashcard generation services."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from flashcards_generator.domain.entities import Deck, Flashcard


@dataclass(frozen=True)
class GenerationConfig:
    """Configuration for flashcard generation."""

    difficulty: str = "medium"
    quantity: str = "standard"
    instructions: str = ""
    timeout_seconds: int = 900
    wait_for_completion: bool = True


@dataclass
class GenerationResult:
    """Result of flashcard generation."""

    deck: Deck
    artifact_id: str | None = None
    completed: bool = False


class FlashcardGeneratorPort(ABC):
    """Port for generating flashcards from PDF sources.

    Implementations:
        - NotebookLMAdapter: Uses Google NotebookLM API
        - MockGenerator: For testing
    """

    @abstractmethod
    def create_notebook(self, title: str) -> str:
        """Create a new notebook and return its ID."""
        pass

    @abstractmethod
    def add_source(self, notebook_id: str, pdf_path: Path) -> str:
        """Add a PDF source to a notebook. Returns source ID."""
        pass

    @abstractmethod
    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = 600
    ) -> bool:
        """Wait for source processing to complete."""
        pass

    @abstractmethod
    def generate_flashcards(
        self,
        notebook_id: str,
        config: GenerationConfig,
    ) -> str | None:
        """Generate flashcards. Returns artifact ID or None."""
        pass

    @abstractmethod
    def wait_for_artifact(
        self, notebook_id: str, artifact_id: str, timeout: int = 900
    ) -> bool:
        """Wait for artifact generation to complete."""
        pass

    @abstractmethod
    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        """Download generated flashcards to file."""
        pass

    @abstractmethod
    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        """Parse flashcards from downloaded JSON."""
        pass

    @abstractmethod
    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete a notebook. Returns success status."""
        pass
