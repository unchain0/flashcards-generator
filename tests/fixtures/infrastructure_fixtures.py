from unittest.mock import MagicMock

import pytest

from flashcards_generator.application.converter import ClozeConverter
from flashcards_generator.application.exporter import DeckExporter
from flashcards_generator.infrastructure.notebooklm_client import NotebookLMClient


@pytest.fixture
def mock_notebooklm_client():
    client = MagicMock(spec=NotebookLMClient)
    client.create_notebook.return_value = "nb123"
    client.add_source.return_value = "src456"
    client.wait_for_source.return_value = True
    client.generate_flashcards.return_value = "art789"
    client.wait_for_artifact.return_value = True
    client.download_flashcards.return_value = True
    client.parse_flashcards.return_value = []
    return client


@pytest.fixture
def mock_subprocess_run(mocker):
    return mocker.patch("subprocess.run")


@pytest.fixture
def cloze_converter():
    return ClozeConverter()


@pytest.fixture
def deck_exporter():
    return DeckExporter()
