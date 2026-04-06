"""Tests for NotebookLM adapter."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.domain.ports.flashcard_generator import GenerationConfig


def mock_popen(returncode=0, stdout="", stderr=""):
    """Helper to create mock Popen object."""
    mock_process = MagicMock()
    mock_process.returncode = returncode
    mock_process.communicate.return_value = (stdout, stderr)
    return mock_process


class TestNotebookLMAdapter:
    """Test suite for NotebookLM adapter."""

    def test_init(self):
        """Test adapter initialization."""
        adapter = NotebookLMAdapter("/path/to/notebooklm", timeout=120)
        assert adapter.notebooklm_path == "/path/to/notebooklm"
        assert adapter.timeout == 120

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_create_notebook(self, mock_popen_class):
        """Test notebook creation."""
        mock_popen_class.return_value = mock_popen(
            returncode=0, stdout='{"id": "nb123"}', stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.create_notebook("Test Notebook")

        assert result == "nb123"
        mock_popen_class.assert_called_once()

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_create_notebook_failure(self, mock_popen_class):
        """Test notebook creation failure."""
        mock_popen_class.return_value = mock_popen(
            returncode=1, stdout="", stderr="Error"
        )

        from flashcards_generator.domain.exceptions import GenerationError

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(GenerationError):
            adapter.create_notebook("Test")

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_add_source(self, mock_popen_class):
        """Test adding source."""
        mock_popen_class.return_value = mock_popen(
            returncode=0, stdout='{"source_id": "src456"}', stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.add_source("nb123", Path("/path/to/file.pdf"))

        assert result == "src456"

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_wait_for_source_success(self, mock_popen_class):
        """Test waiting for source."""
        mock_popen_class.return_value = mock_popen(returncode=0)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.wait_for_source("nb123", "src456")

        assert result is True

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_generate_flashcards_success(self, mock_popen_class):
        """Test flashcard generation."""
        mock_popen_class.return_value = mock_popen(
            returncode=0, stdout='{"task_id": "art789"}', stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig(difficulty="medium", quantity="standard")
        result = adapter.generate_flashcards("nb123", config)

        assert result == "art789"

    @patch("flashcards_generator.adapters.notebooklm_adapter.time.sleep")
    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_generate_flashcards_with_rate_limit(self, mock_popen_class, mock_sleep):
        """Test rate limit retry."""
        mock_popen_class.side_effect = [
            mock_popen(
                returncode=0,
                stdout='{"task_id": "art789"}',
                stderr="GENERATION_FAILED due to rate limit",
            ),
            mock_popen(returncode=0, stdout='{"task_id": "art789"}', stderr=""),
        ]

        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig()
        result = adapter.generate_flashcards("nb123", config)

        assert result == "art789"
        assert mock_popen_class.call_count == 2
        mock_sleep.assert_called_once()

    def test_parse_flashcards(self, tmp_path):
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

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_create_notebook_no_id_in_response(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(
            returncode=0, stdout='{"other": "data"}', stderr=""
        )
        from flashcards_generator.domain.exceptions import GenerationError

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(GenerationError):
            adapter.create_notebook("Test")

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_add_source_no_id_in_response(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(
            returncode=0, stdout='{"other": "data"}', stderr=""
        )
        from flashcards_generator.domain.exceptions import SourceProcessingError

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(SourceProcessingError):
            adapter.add_source("nb123", Path("/path/to/file.pdf"))

    def test_build_generate_command_with_instructions(self):
        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig(instructions="Custom instructions")
        cmd = adapter._build_generate_command("nb123", config)
        # shlex.quote may wrap the string in quotes, so check both cases
        assert any("Custom instructions" in str(arg) for arg in cmd)

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_generate_flashcards_json_decode_error(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(
            returncode=0, stdout="invalid json", stderr=""
        )

        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig()
        result = adapter.generate_flashcards("nb123", config)

        assert result is None

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_generate_flashcards_timeout(self, mock_popen_class):
        mock_popen_class.side_effect = subprocess.TimeoutExpired("cmd", 10)

        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig()
        result = adapter.generate_flashcards("nb123", config)

        assert result is None

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_wait_for_artifact_success(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(returncode=0)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.wait_for_artifact("nb123", "art789")

        assert result is True

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_wait_for_artifact_failure(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(returncode=1)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.wait_for_artifact("nb123", "art789")

        assert result is False

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_download_flashcards_success(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(returncode=0)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.download_flashcards("nb123", "art789", Path("/tmp/out.json"))

        assert result is True

    @patch("flashcards_generator.adapters.notebooklm_adapter.time.sleep")
    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_download_flashcards_error_after_retries(
        self, mock_popen_class, mock_sleep
    ):
        """Test download fails after all retries are exhausted."""
        from flashcards_generator.domain.exceptions import ArtifactDownloadError

        mock_popen_class.return_value = mock_popen(
            returncode=1, stdout="", stderr="Error"
        )

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(ArtifactDownloadError):
            adapter.download_flashcards("nb123", "art789", Path("/tmp/out.json"))

        # Should have tried 3 times
        assert mock_popen_class.call_count == 3
        # Should have slept twice (between retries)
        assert mock_sleep.call_count == 2

    @patch("flashcards_generator.adapters.notebooklm_adapter.time.sleep")
    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_download_flashcards_success_on_retry(self, mock_popen_class, mock_sleep):
        """Test download succeeds on second attempt."""
        # First call fails, second succeeds
        mock_popen_class.side_effect = [
            mock_popen(returncode=1, stdout="", stderr="Rate limited"),
            mock_popen(returncode=0, stdout="", stderr=""),
        ]

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.download_flashcards("nb123", "art789", Path("/tmp/out.json"))

        assert result is True
        assert mock_popen_class.call_count == 2
        assert mock_sleep.call_count == 1

    def test_extract_cards_data_with_flashcards_key(self):
        adapter = NotebookLMAdapter("notebooklm")
        data = {"flashcards": [{"front": "Q1", "back": "A1"}]}
        result = adapter._extract_cards_data(data)
        assert result == [{"front": "Q1", "back": "A1"}]

    def test_create_flashcard_empty(self):
        adapter = NotebookLMAdapter("notebooklm")
        result = adapter._create_flashcard({"front": "", "back": ""})
        assert result is None

    def test_parse_flashcards_json_error(self, tmp_path):
        json_file = tmp_path / "bad.json"
        json_file.write_text("invalid json")

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.parse_flashcards(json_file)

        assert result == []

    def test_parse_flashcards_os_error(self, tmp_path):
        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.parse_flashcards(Path("/nonexistent/path/file.json"))

        assert result == []

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_delete_notebook_success(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(returncode=0)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.delete_notebook("nb123")

        assert result is True

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_delete_notebook_error(self, mock_popen_class):
        mock_popen_class.return_value = mock_popen(
            returncode=1, stdout="", stderr="Error"
        )

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.delete_notebook("nb123")

        assert result is False

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_delete_notebook_exception(self, mock_popen_class):
        """Test delete notebook when subprocess raises exception."""
        mock_popen_class.side_effect = subprocess.CalledProcessError(1, "cmd")

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.delete_notebook("nb123")

        assert result is False

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_delete_notebook_silent_mode(self, mock_popen_class):
        """Test delete notebook with silent mode (no logs)."""
        mock_popen_class.return_value = mock_popen(returncode=0)

        adapter = NotebookLMAdapter("notebooklm")
        result = adapter.delete_notebook("nb123", silent=True)

        assert result is True

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_run_command_keyboard_interrupt(self, mock_popen_class):
        """Test KeyboardInterrupt handling in _run_command."""
        mock_process = MagicMock()
        mock_process.communicate.side_effect = KeyboardInterrupt()
        mock_popen_class.return_value = mock_process

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(KeyboardInterrupt):
            adapter._run_command(["create", "test"])

        mock_process.terminate.assert_called_once()

    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_run_command_keyboard_interrupt_kill(self, mock_popen_class):
        """Test KeyboardInterrupt handling when terminate times out."""
        mock_process = MagicMock()
        mock_process.communicate.side_effect = KeyboardInterrupt()
        mock_process.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        mock_popen_class.return_value = mock_process

        adapter = NotebookLMAdapter("notebooklm")
        with pytest.raises(KeyboardInterrupt):
            adapter._run_command(["create", "test"])

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
