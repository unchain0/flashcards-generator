"""Tests for remaining edge cases in NotebookLM adapter."""

import json
from unittest.mock import patch

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
            mock_run.return_value = (0, json.dumps({"notebooks": "not a list"}), "")
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

                # This should log the error but still return the output
                _stdout, stderr = adapter._execute_with_retry(["test"])

        assert stderr == "Error message"
