"""Port for exporting generated decks to Anki."""

from __future__ import annotations

from abc import ABC, abstractmethod

from flashcards_generator.domain.entities import Deck


class AnkiExporterPort(ABC):
    """Export a generated deck to a configured Anki destination."""

    @abstractmethod
    def export(self, deck: Deck) -> int:
        """Export a deck and return the number of imported notes."""
        # pragma: no cover
