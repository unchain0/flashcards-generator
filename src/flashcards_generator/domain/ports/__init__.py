"""Domain ports (interfaces) for Hexagonal Architecture."""

from __future__ import annotations

from flashcards_generator.domain.ports.chunk_state import ChunkStatePort
from flashcards_generator.domain.ports.deck_repository import (
    DeckRepositoryPort,
)
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
    GenerationResult,
)

__all__ = [
    "ChunkStatePort",
    "DeckRepositoryPort",
    "FlashcardGeneratorPort",
    "GenerationConfig",
    "GenerationResult",
]
