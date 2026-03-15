"""Tests for NotebookLM adapter."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.domain.ports.flashcard_generator import GenerationConfig


class TestNotebookLMAdapter:
    """Test suite for NotebookLM adapter."""

    def test_init(self):
        """Test adapter initialization."""
        adapter = NotebookLMAdapter("/path/to/notebooklm", timeout=120)
        assert adapter.notebooklm_path == "/path/to/notebooklm"
        assert adapter.timeout == 120

    @patch("subprocess.run")
    def test_create_notebook(self, mock_run):
        """Test notebook creation."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"id": "nb123"}', stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.create_notebook("Test Notebook")

        assert result == "nb123"
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notebooklm"
        assert "create" in args

    @patch("subprocess.run")
    def test_create_notebook_failure(self, mock_run):
        """Test notebook creation failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        from flashcards_generator.domain.exceptions import GenerationError

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(GenerationError):
            adapter.create_notebook("Test")

    @patch("subprocess.run")
    def test_add_source(self, mock_run):
        """Test adding source."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"source_id": "src456"}', stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.add_source("nb123", Path("/path/to/file.pdf"))

        assert result == "src456"

    @patch("subprocess.run")
    def test_wait_for_source_success(self, mock_run):
        """Test waiting for source."""
        mock_run.return_value = MagicMock(returncode=0)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.wait_for_source("nb123", "src456")

        assert result is True

    @patch("subprocess.run")
    def test_generate_flashcards_success(self, mock_run):
        """Test flashcard generation."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"task_id": "art789"}', stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig(difficulty="medium", quantity="standard")
        result = adapter.generate_flashcards("nb123", config)

        assert result == "art789"

    @patch("subprocess.run")
    def test_generate_flashcards_with_rate_limit(self, mock_run):
        """Test rate limit retry."""
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout='{"task_id": "art789"}',
                stderr="GENERATION_FAILED due to rate limit",
            ),
            MagicMock(returncode=0, stdout='{"task_id": "art789"}', stderr=""),
        ]

        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig()
        result = adapter.generate_flashcards("nb123", config)

        assert result == "art789"
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_parse_flashcards(self, mock_run, tmp_path):
        """Test parsing flashcards."""
        json_data = [
            {"front": "Question 1?", "back": "Answer 1"},
            {"question": "Question 2?", "answer": "Answer 2"},
        ]

        json_file = tmp_path / "flashcards.json"
        json_file.write_text(json.dumps(json_data))

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.parse_flashcards(json_file)

        assert len(result) == 2
        assert result[0].front == "Question 1?"
        assert result[0].back == "Answer 1"
