"""Filesystem-backed repository for chunk resume state."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from flashcards_generator.domain.entities import ChunkResumeManifest, Deck
from flashcards_generator.domain.ports import ChunkStatePort
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger("chunk_state_repository")


class FileSystemChunkStateRepository(ChunkStatePort):
    """Persist chunk manifests and result decks as JSON files."""

    def load_manifest(self, state_path: Path) -> ChunkResumeManifest | None:
        """Load a chunk resume manifest from disk if present."""
        if not state_path.exists():
            logger.debug(f"Manifest not found: {state_path}")
            return None

        logger.debug(f"Loading manifest from {state_path}")
        return ChunkResumeManifest.model_validate_json(state_path.read_text())

    def save_manifest(
        self, state_path: Path, manifest: ChunkResumeManifest
    ) -> None:
        """Persist a chunk resume manifest to disk atomically."""
        logger.debug(f"Saving manifest to {state_path}")
        self._atomic_write(state_path, manifest.model_dump_json(indent=2))

    def delete_manifest(self, state_path: Path) -> None:
        """Delete a persisted chunk manifest if it exists."""
        if state_path.exists():
            logger.debug(f"Deleting manifest at {state_path}")
            state_path.unlink()

    def save_chunk_result(self, path: Path, deck: Deck) -> None:
        """Persist a chunk result deck to disk atomically."""
        logger.debug(f"Saving chunk result to {path}")
        self._atomic_write(path, deck.model_dump_json(indent=2))

    def load_chunk_result(self, path: Path) -> Deck:
        """Load a persisted chunk result deck from disk."""
        logger.debug(f"Loading chunk result from {path}")
        return Deck.model_validate_json(path.read_text())

    def delete_chunk_results(self, dir_path: Path) -> None:
        """Delete a directory containing persisted chunk result files."""
        if dir_path.exists():
            logger.debug(f"Deleting chunk results directory {dir_path}")
            shutil.rmtree(dir_path)

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content atomically using a temporary sibling file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")

        try:
            temp_path.write_text(content)
            temp_path.replace(path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
