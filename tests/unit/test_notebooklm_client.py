from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.integrations.notebooklm.client import (
    NotebookLMClient,
)


class TestNotebookLMClient:
    def test_init(self):
        client = NotebookLMClient("/path/to/notebooklm", timeout=120)
        assert client.notebooklm_path == "/path/to/notebooklm"
        assert client.timeout == 120

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
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

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error"
        )

        client = NotebookLMClient("notebooklm")
        with pytest.raises(RuntimeError):
            client.create_notebook("Test")

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_add_source(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"source_id": "src456"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        result = client.add_source("nb123", Path("/path/to/file.pdf"))

        assert result == "src456"

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_wait_for_source_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_source("nb123", "src456")

        assert result is True

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_wait_for_source_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_source("nb123", "src456")

        assert result is False

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_generate_flashcards_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"artifact_id": "art789"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        result = client.generate_flashcards("nb123", "Generate flashcards")

        assert result == "art789"

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_generate_flashcards_failure(self, mock_run):
        mock_run.side_effect = Exception("Connection error")

        client = NotebookLMClient("notebooklm")

        with pytest.raises(Exception, match="Connection error"):
            client.generate_flashcards("nb123", "Generate flashcards")

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_generate_flashcards_returns_none_for_process_error(
        self, mock_run
    ):
        mock_run.side_effect = RuntimeError("CLI unavailable")

        assert (
            NotebookLMClient("notebooklm").generate_flashcards(
                "nb123", "prompt"
            )
            is None
        )

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_wait_for_artifact_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_artifact("nb123", "art789")

        assert result is True

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_wait_for_artifact_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        client = NotebookLMClient("notebooklm")
        result = client.wait_for_artifact("nb123", "art789")

        assert result is False

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_download_flashcards_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient("notebooklm")
        output_path = Path("/tmp/output.json")
        result = client.download_flashcards("nb123", "art789", output_path)

        assert result is True

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_download_flashcards_failure(self, mock_run):
        mock_run.side_effect = RuntimeError("Download error")

        client = NotebookLMClient("notebooklm")
        output_path = Path("/tmp/output.json")
        result = client.download_flashcards("nb123", "art789", output_path)

        assert result is False

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_no_id_in_response(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"other": "data"}', stderr=""
        )

        client = NotebookLMClient("notebooklm")
        with pytest.raises(RuntimeError):
            client.create_notebook("Test")

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_delete_notebook_called_process_error(self, mock_run):
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "cmd")

        client = NotebookLMClient("notebooklm")
        result = client.delete_notebook("nb123")

        assert result is False

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_wait_timeout_is_the_subprocess_deadline(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client = NotebookLMClient("notebooklm", timeout=900)

        assert client.wait_for_source("nb123", "src456", timeout=7) is True
        assert mock_run.call_args.kwargs["timeout"] == 7

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_generate_and_delete_use_adapter_cli_dialect(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout='{"task_id": "art789"}', stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        client = NotebookLMClient("notebooklm")

        assert client.generate_flashcards("nb123", "prompt text") == "art789"
        assert client.delete_notebook("nb123") is True

        assert mock_run.call_args_list[0].args[0] == [
            "notebooklm",
            "generate",
            "flashcards",
            "--notebook",
            "nb123",
            "--difficulty",
            "medium",
            "--quantity",
            "standard",
            "--json",
            "prompt text",
        ]
        assert mock_run.call_args_list[1].args[0] == [
            "notebooklm",
            "delete",
            "-n",
            "nb123",
            "-y",
        ]

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_delete_notebook_returns_false_for_nonzero_status(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="denied"
        )
        client = NotebookLMClient("notebooklm")

        assert client.delete_notebook("nb123") is False

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_rejects_wrong_json_shape(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        client = NotebookLMClient("notebooklm")

        with pytest.raises(RuntimeError, match="response"):
            client.create_notebook("notebook")

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_delete_notebook_timeout_expired(self, mock_run):
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired("cmd", 10)

        client = NotebookLMClient("notebooklm")
        result = client.delete_notebook("nb123")

        assert result is False
