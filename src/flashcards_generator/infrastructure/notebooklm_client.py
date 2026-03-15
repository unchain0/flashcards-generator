"""Client for interacting with NotebookLM CLI."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger("notebooklm_client")


class NotebookLMClient:
    """Client for interacting with NotebookLM CLI."""

    def __init__(self, notebooklm_path: str, timeout: int = 900):
        """Initialize client with notebooklm CLI path."""
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout

    def _run(self, args: list[str], check: bool = True) -> tuple[int, str, str]:
        """Execute notebooklm CLI command."""
        cmd = [self.notebooklm_path, *args]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed: {result.stderr}")
        return result.returncode, result.stdout, result.stderr

    def create_notebook(self, title: str) -> str:
        """Create a new notebook."""
        _, stdout, _ = self._run(["create", title, "--json"])
        data: dict = json.loads(stdout)
        notebook_id = data.get("id") or data.get("notebook", {}).get("id")
        if not notebook_id:
            raise RuntimeError(f"Failed to create notebook: {data}")
        return str(notebook_id)

    def add_source(self, notebook_id: str, file_path: Path) -> str:
        """Add a source file to a notebook."""
        cmd = ["source", "add", str(file_path), "--notebook", notebook_id, "--json"]
        _, stdout, _ = self._run(cmd)
        data: dict = json.loads(stdout)
        source_id = data.get("source_id") or data.get("source", {}).get("id")
        return str(source_id) if source_id else ""

    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = 600
    ) -> bool:
        """Wait for source processing to complete."""
        cmd = [
            "source",
            "wait",
            source_id,
            "-n",
            notebook_id,
            "--timeout",
            str(timeout),
        ]
        returncode, _, _ = self._run(cmd, check=False)
        return returncode == 0

    def generate_flashcards(
        self,
        notebook_id: str,
        prompt: str,
        difficulty: str = "medium",
        quantity: str = "standard",
    ) -> str | None:
        """Generate flashcards artifact from notebook."""
        cmd = [
            "generate",
            "flashcards",
            "-n",
            notebook_id,
            "--prompt",
            prompt,
            "--difficulty",
            difficulty,
            "--quantity",
            quantity,
            "--json",
        ]
        try:
            _, stdout, _ = self._run(cmd)
            data: dict = json.loads(stdout)
            artifact_id = data.get("artifact_id") or data.get("id")
            return str(artifact_id) if artifact_id else None
        except json.JSONDecodeError, subprocess.CalledProcessError:
            return None

    def wait_for_artifact(
        self, notebook_id: str, artifact_id: str, timeout: int = 900
    ) -> bool:
        """Wait for artifact generation to complete."""
        cmd = [
            "artifact",
            "wait",
            artifact_id,
            "-n",
            notebook_id,
            "--timeout",
            str(timeout),
        ]
        returncode, _, _ = self._run(cmd, check=False)
        return returncode == 0

    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        """Download flashcards artifact to file."""
        cmd = [
            "download",
            "flashcards",
            "-n",
            notebook_id,
            "-a",
            artifact_id,
            "--format",
            "json",
            str(output_path),
        ]
        try:
            self._run(cmd)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"Download failed: {e}") from e

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        """Parse flashcards from JSON file."""
        flashcards: list[Flashcard] = []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            cards_data = data if isinstance(data, list) else data.get("cards", [])

            for item in cards_data:
                card = self._create_flashcard(item)
                if card:
                    flashcards.append(card)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to parse flashcards from {json_path}: {e}")
        return flashcards

    def _create_flashcard(self, item: dict) -> Flashcard | None:
        """Create Flashcard from JSON item."""
        front = item.get("front", item.get("question", item.get("q", "")))
        back = item.get("back", item.get("answer", item.get("a", "")))
        if front and back:
            return Flashcard(front=front, back=back)
        return None

    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete notebook (best effort)."""
        try:
            self._run(["notebook", "delete", notebook_id, "--force"], check=False)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to delete notebook {notebook_id}: {e}")
            return False
