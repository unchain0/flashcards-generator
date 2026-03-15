"""Domain entities for flashcard generation."""

from datetime import datetime

from pydantic import BaseModel, Field


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
