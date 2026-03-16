"""Port for deck persistence."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from flashcards_generator.domain.entities import Deck


class DeckRepositoryPort(ABC):
    """Port for persisting and retrieving decks.

    Implementations:
        - FileSystemDeckRepository: Saves to CSV/JSON files
        - InMemoryDeckRepository: For testing
    """

    @abstractmethod
    def save(self, deck: Deck, output_path: Path) -> Path:
        """Save deck to storage. Returns the saved file path."""
        pass  # pragma: no cover

    @abstractmethod
    def load(self, path: Path) -> Deck:
        """Load deck from storage."""
        pass  # pragma: no cover

    @abstractmethod
    def exists(self, deck_name: str, output_path: Path) -> bool:
        """Check if deck already exists."""
        pass  # pragma: no cover
