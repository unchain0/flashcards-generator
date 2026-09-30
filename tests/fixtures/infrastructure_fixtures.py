from unittest.mock import MagicMock

import pytest

from flashcards_generator.engines.cloze import ClozeConverter
from flashcards_generator.integrations.deck_exporter import DeckExporter
from flashcards_generator.integrations.notebooklm.client import (
    NotebookLMClient,
)


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
def mock_bounded_process_output(monkeypatch):
    def capture(process, *, timeout):
        return process.communicate(timeout=timeout)

    for module in (
        "notebooklm.process_runner",
        "notebooklm.management",
        "pptx_converter",
    ):
        monkeypatch.setattr(
            f"flashcards_generator.integrations.{module}.communicate_bounded",
            capture,
        )


@pytest.fixture
def cloze_converter():
    return ClozeConverter()


@pytest.fixture
def deck_exporter():
    return DeckExporter()
