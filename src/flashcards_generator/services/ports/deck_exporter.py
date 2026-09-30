from __future__ import annotations

from pathlib import Path
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck


class DeckExporterPort(Protocol):
    def export_csv(self, deck: Deck, path: Path) -> None: ...
