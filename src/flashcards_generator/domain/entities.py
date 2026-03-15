from datetime import datetime

from pydantic import BaseModel, Field


class Flashcard(BaseModel):
    front: str = Field(description="Frente do card (pergunta ou cloze)")
    back: str = Field(description="Verso do card (resposta)")
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="")

    def to_anki_format(self) -> str:
        tags_str = " ".join(self.tags)
        return f"{self.front}\t{self.back}\t{tags_str}"


class Deck(BaseModel):
    name: str
    description: str = Field(default="")
    flashcards: list[Flashcard] = Field(default_factory=list)
    notebook_id: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def total_cards(self) -> int:
        return len(self.flashcards)

    def add_flashcard(self, card: Flashcard) -> None:
        self.flashcards.append(card)
