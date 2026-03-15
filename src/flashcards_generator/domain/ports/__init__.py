"""Domain ports (interfaces) for Hexagonal Architecture."""

from flashcards_generator.domain.ports.deck_repository import DeckRepositoryPort
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
    GenerationResult,
)

__all__ = [
    "DeckRepositoryPort",
    "FlashcardGeneratorPort",
    "GenerationConfig",
    "GenerationResult",
]
