import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.infrastructure.notebooklm_client import NotebookLMClient


class TestNotebookLMClient:
    def test_init(self):
        client = NotebookLMClient("/path/to/notebooklm", timeout=120)
        assert client.notebooklm_path == "/path/to/notebooklm"
        assert client.timeout == 120

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_create_notebook(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"id": "nb123"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        result = client.create_notebook("Test Notebook")

        assert result == "nb123"
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notebooklm"
        assert "create" in args

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_create_notebook_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        client = NotebookLMClient("notebooklm")
        with pytest.raises(RuntimeError):
            client.create_notebook("Test")

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_add_source(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"source_id": "src456"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        result = client.add_source("nb123", Path("/path/to/file.pdf"))

        assert result == "src456"

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_wait_for_source_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_source("nb123", "src456")

        assert result is True

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_wait_for_source_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_source("nb123", "src456")

        assert result is False

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_generate_flashcards_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"artifact_id": "art789"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        result = client.generate_flashcards("nb123", "Generate flashcards")

        assert result == "art789"

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_generate_flashcards_failure(self, mock_run):
        mock_run.side_effect = Exception("Connection error")

        client = NotebookLMClient("notebooklm")
        result = client.generate_flashcards("nb123", "Generate flashcards")

        assert result is None

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_wait_for_artifact_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_artifact("nb123", "art789")

        assert result is True

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_wait_for_artifact_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_artifact("nb123", "art789")

        assert result is False

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_download_flashcards_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient("notebooklm")
        output_path = Path("/tmp/output.json")
        result = client.download_flashcards("nb123", "art789", output_path)

        assert result is True

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_download_flashcards_failure(self, mock_run):
        mock_run.side_effect = RuntimeError("Download error")

        client = NotebookLMClient("notebooklm")
        output_path = Path("/tmp/output.json")
        result = client.download_flashcards("nb123", "art789", output_path)

        assert result is False

    def test_parse_flashcards(self, tmp_path):
        json_data = [
            {"front": "Question 1?", "back": "Answer 1"},
            {"question": "Question 2?", "answer": "Answer 2"},
            {"q": "Question 3?", "a": "Answer 3"},
        ]

        json_file = tmp_path / "flashcards.json"
        json_file.write_text(json.dumps(json_data))

        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(json_file)

        assert len(result) == 3
        assert isinstance(result[0], Flashcard)
        assert result[0].front == "Question 1?"
        assert result[0].back == "Answer 1"

    def test_parse_flashcards_with_flashcards_key(self, tmp_path):
        json_data = {
            "flashcards": [
                {"front": "Q1?", "back": "A1"},
                {"front": "Q2?", "back": "A2"},
            ]
        }

        json_file = tmp_path / "flashcards.json"
        json_file.write_text(json.dumps(json_data))

        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(json_file)

        assert len(result) == 2

    def test_parse_flashcards_empty(self, tmp_path):
        json_file = tmp_path / "flashcards.json"
        json_file.write_text("[]")

        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(json_file)

        assert len(result) == 0

    def test_parse_flashcards_invalid_json(self, tmp_path):
        json_file = tmp_path / "flashcards.json"
        json_file.write_text("invalid json")

        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(json_file)

        assert len(result) == 0

    def test_parse_flashcards_file_not_found(self, tmp_path):
        json_file = tmp_path / "nonexistent.json"

        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(json_file)

        assert len(result) == 0

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_create_notebook_no_id_in_response(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"other": "data"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        with pytest.raises(RuntimeError):
            client.create_notebook("Test")

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_parse_flashcards_empty_cards_key(self, mock_run, tmp_path):
        json_data = {"cards": []}
        json_file = tmp_path / "flashcards.json"
        json_file.write_text(json.dumps(json_data))

        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(json_file)

        assert result == []

    def test_parse_flashcards_nonexistent_path(self):
        client = NotebookLMClient("notebooklm")
        result = client.parse_flashcards(Path("/nonexistent/file.json"))

        assert result == []

    def test_create_flashcard_empty_front(self):
        client = NotebookLMClient("notebooklm")
        result = client._create_flashcard({"front": "", "back": "answer"})
        assert result is None

    def test_create_flashcard_empty_back(self):
        client = NotebookLMClient("notebooklm")
        result = client._create_flashcard({"front": "question", "back": ""})
        assert result is None

    def test_create_flashcard_both_empty(self):
        client = NotebookLMClient("notebooklm")
        result = client._create_flashcard({"front": "", "back": ""})
        assert result is None

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_delete_notebook_called_process_error(self, mock_run):
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "cmd")

        client = NotebookLMClient("notebooklm")
        result = client.delete_notebook("nb123")

        assert result is False

    @patch("flashcards_generator.infrastructure.notebooklm_client.subprocess.run")
    def test_delete_notebook_timeout_expired(self, mock_run):
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired("cmd", 10)

        client = NotebookLMClient("notebooklm")
        result = client.delete_notebook("nb123")

        assert result is False
