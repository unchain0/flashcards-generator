import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.domain.ports.flashcard_generator import (
    GenerationConfig,
)
from flashcards_generator.infrastructure.notebooklm_client import (
    NotebookLMClient,
)


def _assert_fake_cli_calls(recorded: list[list[str]]) -> None:
    """Assert both boundaries use the same fake CLI argument contract."""
    assert recorded == [
        [
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
        ],
        [
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
        ],
        ["delete", "-n", "nb123", "-y"],
        ["delete", "-n", "nb123", "-y"],
    ]


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

    def test_adapter_and_client_share_fake_cli_contract(self, tmp_path):
        """Both boundaries use the selected argv dialect without live auth."""
        cli = tmp_path / "fake_notebooklm.py"
        argv_log = tmp_path / "argv.jsonl"
        cli.write_text(
            "#!" + sys.executable + "\n"
            "import json, sys\n"
            f"log = {str(argv_log)!r}\n"
            "args = sys.argv[1:]\n"
            "with open(log, 'a', encoding='utf-8') as file:\n"
            "    file.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['generate', 'flashcards'] and "
            "'--notebook' in args and '--json' in args:\n"
            "    print(json.dumps({'task_id': 'art789'}))\n"
            "elif args[:1] == ['delete'] and args[-1:] == ['-y']:\n"
            "    pass\n"
            "else:\n"
            "    print('invalid argv', file=sys.stderr)\n"
            "    raise SystemExit(2)\n",
            encoding="utf-8",
        )
        cli.chmod(0o755)

        adapter = NotebookLMAdapter(str(cli))
        client = NotebookLMClient(str(cli))
        config = GenerationConfig(instructions="prompt text")

        assert adapter.generate_flashcards("nb123", config) == "art789"
        assert client.generate_flashcards("nb123", "prompt text") == "art789"
        assert adapter.delete_notebook("nb123") is True
        assert client.delete_notebook("nb123") is True
        recorded = [
            json.loads(line)
            for line in Path(argv_log).read_text().splitlines()
        ]
        _assert_fake_cli_calls(recorded)
