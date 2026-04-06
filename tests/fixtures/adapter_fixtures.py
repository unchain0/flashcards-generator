"""Fixtures for adapter testing."""

from typing import TYPE_CHECKING

import pytest

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.domain.exceptions import SourceProcessingError
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


class MockFlashcardGenerator(FlashcardGeneratorPort):
    """Mock implementation for testing."""

    def __init__(
        self,
        should_fail_source: bool = False,
        should_fail_generation: bool = False,
        should_timeout: bool = False,
        flashcards: list | None = None,
    ):
        self.should_fail_source = should_fail_source
        self.should_fail_generation = should_fail_generation
        self.should_timeout = should_timeout
        self.flashcards = flashcards or []
        self._notebooks: dict = {}
        self._sources: dict = {}
        self._artifacts: dict = {}
        self._artifact_counter = 0

    def create_notebook(self, title: str) -> str:
        notebook_id = f"nb{len(self._notebooks) + 1}"
        self._notebooks[notebook_id] = {"title": title, "sources": []}
        return notebook_id

    def add_source(self, notebook_id: str, pdf_path: Path) -> str:
        if self.should_fail_source:
            raise SourceProcessingError(pdf_path, "Mock source failure")
        source_id = f"src{len(self._sources) + 1}"
        self._sources[source_id] = {"notebook": notebook_id, "path": pdf_path}
        self._notebooks[notebook_id]["sources"].append(source_id)
        return source_id

    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = 600
    ) -> bool:
        return True

    def generate_flashcards(
        self, notebook_id: str, config: GenerationConfig
    ) -> str | None:
        if self.should_fail_generation:
            return None
        self._artifact_counter += 1
        artifact_id = f"art{self._artifact_counter}"
        self._artifacts[artifact_id] = {
            "notebook": notebook_id,
            "flashcards": self.flashcards,
        }
        return artifact_id

    def wait_for_artifact(
        self, notebook_id: str, artifact_id: str, timeout: int = 900
    ) -> bool:
        return not self.should_timeout

    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        import json

        cards = self._artifacts.get(artifact_id, {}).get("flashcards", [])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(cards))
        return True

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        import json

        data = json.loads(json_path.read_text())
        return [Flashcard(front=item["front"], back=item["back"]) for item in data]

    def delete_notebook(self, notebook_id: str) -> bool:
        self._notebooks.pop(notebook_id, None)
        return True


@pytest.fixture
def mock_generator():
    """Create mock generator fixture."""
    return MockFlashcardGenerator


@pytest.fixture
def sample_flashcards():
    """Sample flashcards for testing."""
    return [
        {"front": "What is Python?", "back": "A programming language"},
        {"front": "What is FastAPI?", "back": "A modern web framework"},
    ]
