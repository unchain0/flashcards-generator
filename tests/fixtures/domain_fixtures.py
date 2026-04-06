from datetime import datetime
from pathlib import Path

import pytest

from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.domain.value_objects import Config, SourceInfo


@pytest.fixture
def sample_flashcard():
    return Flashcard(
        front="Qual é a capital da França?",
        back="Paris",
        tags=["geografia", "europa"],
        source="aula1.pdf",
    )


@pytest.fixture
def sample_deck():
    return Deck(
        name="Geografia",
        description="Deck de geografia",
        notebook_id="nb123",
        created_at=datetime(2024, 1, 1),
    )


@pytest.fixture
def deck_with_cards(sample_flashcard):
    deck = Deck(name="História", description="Deck de história")
    deck.add_flashcard(sample_flashcard)
    deck.add_flashcard(
        Flashcard(
            front="Quando foi a Revolução Francesa?",
            back="1789",
            tags=["história", "frança"],
        )
    )
    return deck


@pytest.fixture
def sample_config(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    return Config(
        input_dir=input_dir,
        output_dir=output_dir,
        difficulty="medium",
        quantity="standard",
        instructions="Foque em conceitos importantes",
        wait_for_completion=True,
        timeout=900,
    )


@pytest.fixture
def sample_source_info():
    return SourceInfo(
        source_id="src123", file_path=Path("/tmp/test.pdf"), status="ready"
    )
