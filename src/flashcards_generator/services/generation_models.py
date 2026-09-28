from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from flashcards_generator.domain_models.entities import (
    ChunkResumeManifest,
    Deck,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)

MAX_FILENAME_LEN = 50
SOURCE_WAIT_TIMEOUT = 600
PDF_CHUNKING_THRESHOLD = 50
MIN_CARDS_QUALITY_LENGTH = 10
BORDER_LENGTH = 60
CHUNK_DELAY_SECONDS = 5
CHUNK_RETRY_MAX_ATTEMPTS = 3
CHUNK_RETRY_INITIAL_DELAY = 5
CHUNK_RETRY_MAX_DELAY = 60
CHUNK_RETRY_BACKOFF_MULTIPLIER = 2.0


@dataclass(slots=True)
class _ChunkRun:
    """Mutable state for one chunked document generation run."""

    pdf_path: Path
    deck_name: str
    pdf_output_path: Path
    processing_path: Path
    request: GenerateFlashcardsRequest
    chunks: list[Path] = field(default_factory=list)
    chunk_decks: dict[int, Deck] = field(default_factory=dict)
    manifest: ChunkResumeManifest | None = None
    completed_indexes: set[int] = field(default_factory=set)
    resume_dir: Path | None = None
    state_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _ChunkTask:
    """Immutable inputs for processing one document chunk."""

    chunk_path: Path
    deck_name: str
    pdf_output_path: Path
    request: GenerateFlashcardsRequest
    chunk_index: int
    total_chunks: int


@dataclass(frozen=True, slots=True)
class _ChunkAttemptResult:
    deck: Deck | None
    next_delay_seconds: float | None


def _safe_filename(base_name: str, suffix: str = "") -> str:
    """Create a safe filename that doesn't exceed filesystem limits.

    Args:
        base_name: The base name of the file
        suffix: Optional suffix to append (e.g., "_raw.json")

    Returns:
        A filename that's guaranteed to be within filesystem limits
    """
    total_len = len(base_name) + len(suffix)

    if total_len <= MAX_FILENAME_LEN:
        return f"{base_name}{suffix}"

    # Need to truncate - use hash to preserve uniqueness
    # Format: <truncated>_<hash><suffix>
    hash_len = 8
    separator_len = 1  # for "_"
    available = MAX_FILENAME_LEN - len(suffix) - hash_len - separator_len

    truncated = base_name[:available]
    name_hash = hashlib.md5(base_name.encode()).hexdigest()[:hash_len]

    return f"{truncated}_{name_hash}{suffix}"
