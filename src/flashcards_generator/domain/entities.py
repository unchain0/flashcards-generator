"""Domain entities for flashcard generation."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from enum import StrEnum

from pydantic import BaseModel, Field


class ChunkStatus(StrEnum):
    """Processing status for a chunk."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ChunkState(BaseModel):
    """Resume state for a single processed chunk."""

    chunk_index: int
    status: ChunkStatus
    page_start: int | None = None
    page_end: int | None = None
    card_count: int = 0
    result_path: str | None = None
    updated_at: datetime
    error_message: str | None = None


class ChunkResumeManifest(BaseModel):
    """Persisted state for resuming chunked PDF processing."""

    source_pdf: str
    source_signature: str
    deck_name: str
    total_chunks: int
    chunks: list[ChunkState] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class Flashcard(BaseModel):
    """A single flashcard with front, back and metadata."""

    front: str = Field(description="Frente do card (pergunta ou cloze)")
    back: str = Field(description="Verso do card (resposta)")
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="")

    def to_anki_format(self) -> str:
        """Convert to Anki TSV format."""
        tags_str = " ".join(self.tags)
        return f"{self.front}\t{self.back}\t{tags_str}"

    def normalized_front(self) -> str:
        """Return normalized front text for comparison."""
        return " ".join(self.front.lower().split())


class Deck(BaseModel):
    """A collection of flashcards."""

    name: str
    description: str = Field(default="")
    flashcards: list[Flashcard] = Field(default_factory=list)
    notebook_id: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def total_cards(self) -> int:
        """Return total number of cards in deck."""
        return len(self.flashcards)

    def add_flashcard(self, card: Flashcard) -> None:
        """Add a card to the deck."""
        self.flashcards.append(card)

    def deduplicate(self, similarity_threshold: float = 0.85) -> int:
        """Remove duplicate flashcards based on front content similarity.

        Args:
            similarity_threshold: Minimum similarity ratio (0-1) to consider
                cards as duplicates.

        Returns:
            Number of duplicates removed.
        """
        if not self.flashcards:
            return 0

        unique_cards: list[Flashcard] = []
        removed_count = 0

        for card in self.flashcards:
            is_duplicate = False
            normalized = card.normalized_front()

            for existing in unique_cards:
                existing_normalized = existing.normalized_front()
                similarity = SequenceMatcher(
                    None, normalized, existing_normalized
                ).ratio()

                if similarity >= similarity_threshold:
                    is_duplicate = True
                    removed_count += 1
                    break

            if not is_duplicate:
                unique_cards.append(card)

        self.flashcards = unique_cards
        return removed_count
