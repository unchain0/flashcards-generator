"""Tests for remaining edge cases in NotebookLM adapter."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.adapters.notebooklm_adapter import (
    GenerationConfig,
    NotebookLMAdapter,
)


class TestNotebookLMAdapterEdgeCases:
    """Test edge cases for 100% coverage."""

    def test_list_notebooks_not_list_response(self):
        """Test list_notebooks when response is not a list."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "_run_command") as mock_run:
            # Return a dict where 'notebooks' is not a list
            mock_run.return_value = (
                0,
                json.dumps({"notebooks": "not a list"}),
                "",
            )
            result = adapter.list_notebooks()

        assert result == []

    def test_generate_flashcards_empty_output(self):
        """Test generate_flashcards when output is empty."""
        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig(
            difficulty="medium",
            quantity="standard",
            instructions="",
        )

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = adapter.generate_flashcards("notebook123", config)

        assert result is None

    def test_generate_flashcards_whitespace_output(self):
        """Test generate_flashcards when output is only whitespace."""
        adapter = NotebookLMAdapter("notebooklm")
        config = GenerationConfig(
            difficulty="medium",
            quantity="standard",
            instructions="",
        )

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, "   \n\t  ", "")
            result = adapter.generate_flashcards("notebook123", config)

        assert result is None

    def test_execute_with_retry_logs_error(self):
        """Test that _execute_with_retry logs error on failure."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "Error message")
            with patch.object(adapter, "_needs_retry") as mock_retry:
                mock_retry.return_value = False

                status, _stdout, stderr = adapter._execute_with_retry(
                    ["test"], timeout=900
                )

        assert status == 1
        assert stderr == "Error message"

    def test_generation_rejects_nonzero_status_before_parsing(self):
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (
                1,
                '{"task_id": "stale"}',
                "authentication required",
            )
            result = adapter.generate_flashcards(
                "notebook123", GenerationConfig()
            )

        assert result is None

    def test_create_notebook_rejects_wrong_json_shape(self):
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(
            adapter, "_run_command", return_value=(0, "null", "")
        ):
            from flashcards_generator.domain.exceptions import GenerationError

            with pytest.raises(GenerationError, match="response"):
                adapter.create_notebook("notebook")

    def test_parse_flashcards_rejects_malformed_envelope(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text('{"cards": "not-a-list"}')
        adapter = NotebookLMAdapter("notebooklm")

        with pytest.raises(RuntimeError, match="response"):
            adapter.parse_flashcards(json_path)

    def test_command_logs_metadata_without_command_output(self):
        adapter = NotebookLMAdapter("notebooklm")

        with (
            patch.object(
                adapter, "_run_command", return_value=(0, "SECRET_123", "")
            ),
            patch(
                "flashcards_generator.adapters.notebooklm_adapter.logger"
            ) as mock_logger,
        ):
            adapter._execute_with_retry(
                ["generate", "flashcards"], timeout=900
            )

        messages = [str(call.args[0]) for call in mock_logger.method_calls]
        assert all("SECRET_123" not in message for message in messages)
        assert any("operation=generate" in message for message in messages)
        assert any("status=0" in message for message in messages)

    @patch("flashcards_generator.adapters.notebooklm_adapter.time.sleep")
    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_download_does_not_retry_permanent_auth_failure(
        self, mock_popen_class, mock_sleep
    ):
        from flashcards_generator.domain.exceptions import (
            ArtifactDownloadError,
        )

        mock_popen_class.return_value = MagicMock(
            returncode=1,
            communicate=MagicMock(
                return_value=("", "authentication required")
            ),
        )
        adapter = NotebookLMAdapter("notebooklm")

        with pytest.raises(ArtifactDownloadError):
            adapter.download_flashcards("nb123", "art789", Path("out.json"))

        assert mock_popen_class.call_count == 1
        mock_sleep.assert_not_called()

    @patch("flashcards_generator.adapters.notebooklm_adapter.time.sleep")
    @patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
    def test_generation_does_not_retry_success_with_warning_stderr(
        self, mock_popen_class, mock_sleep
    ):
        mock_popen_class.return_value = MagicMock(
            returncode=0,
            communicate=MagicMock(
                return_value=(
                    '{"task_id": "art789"}',
                    "rate limit policy documentation",
                )
            ),
        )
        adapter = NotebookLMAdapter("notebooklm")

        result = adapter.generate_flashcards("nb123", GenerationConfig())

        assert result == "art789"
        assert mock_popen_class.call_count == 1
        mock_sleep.assert_not_called()
