from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.integration
class TestNotebookLMClientIntegration:
    @patch("subprocess.run")
    def test_create_notebook(self, mock_run):
        from flashcards_generator.infrastructure.notebooklm_client import (
            NotebookLMClient,
        )

        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"id": "nb123"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        result = client.create_notebook("Test Notebook")

        assert result == "nb123"
        mock_run.assert_called_once()
