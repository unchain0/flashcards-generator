"""Port for persisting chunk resume state and results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from flashcards_generator.domain.entities import ChunkResumeManifest, Deck


class ChunkStatePort(ABC):
    """Port for reading and writing chunk resume state."""

    @abstractmethod
    def load_manifest(self, state_path: Path) -> ChunkResumeManifest | None:
        """Load a chunk resume manifest from disk if it exists."""
        # pragma: no cover

    @abstractmethod
    def save_manifest(
        self, state_path: Path, manifest: ChunkResumeManifest
    ) -> None:
        """Persist a chunk resume manifest to disk."""
        # pragma: no cover

    @abstractmethod
    def delete_manifest(self, state_path: Path) -> None:
        """Delete a chunk resume manifest if present."""
        # pragma: no cover

    @abstractmethod
    def save_chunk_result(self, path: Path, deck: Deck) -> None:
        """Persist an individual chunk result deck to disk."""
        # pragma: no cover

    @abstractmethod
    def load_chunk_result(self, path: Path) -> Deck:
        """Load an individual chunk result deck from disk."""
        # pragma: no cover

    @abstractmethod
    def delete_chunk_results(self, dir_path: Path) -> None:
        """Delete persisted chunk result files from a directory."""
        # pragma: no cover
