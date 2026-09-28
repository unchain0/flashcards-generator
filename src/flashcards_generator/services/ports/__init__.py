"""Domain ports (interfaces) for Hexagonal Architecture."""

from __future__ import annotations

from flashcards_generator.services.ports.anki_exporter import AnkiExporterPort
from flashcards_generator.services.ports.chunk_state import ChunkStatePort
from flashcards_generator.services.ports.deck_repository import (
    DeckRepositoryPort,
)
from flashcards_generator.services.ports.document_sources import (
    DocumentSelection,
    DocumentSourcesPort,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
    GenerationResult,
)
from flashcards_generator.services.ports.pdf_chunker import PDFChunkerPort
from flashcards_generator.services.ports.source_snapshots import (
    SourceSnapshotsPort,
)

__all__ = [
    "AnkiExporterPort",
    "ChunkStatePort",
    "DeckRepositoryPort",
    "DocumentSelection",
    "DocumentSourcesPort",
    "FlashcardGeneratorPort",
    "GenerationConfig",
    "GenerationResult",
    "PDFChunkerPort",
    "SourceSnapshotsPort",
]
