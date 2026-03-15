import json
import subprocess
import time
from typing import TYPE_CHECKING

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger("notebooklm_client")


class NotebookLMClient:
    def __init__(self, notebooklm_path: str, timeout: int = 900):
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout

    def _run(self, args: list[str], check: bool = True) -> tuple[int, str, str]:
        cmd = [self.notebooklm_path, *args]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed: {result.stderr}")
        return result.returncode, result.stdout, result.stderr

    def create_notebook(self, title: str) -> str:
        _, stdout, _ = self._run(["create", title, "--json"])
        data = json.loads(stdout)
        notebook_id = data.get("id") or data.get("notebook", {}).get("id")
        if not notebook_id:
            raise RuntimeError(f"Failed to create notebook: {data}")
        return notebook_id

    def add_source(self, notebook_id: str, file_path: Path) -> str:
        cmd = ["source", "add", str(file_path), "--notebook", notebook_id, "--json"]
        _, stdout, _ = self._run(cmd)
        data = json.loads(stdout)
        return data.get("source_id") or data.get("source", {}).get("id")

    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = 600
    ) -> bool:
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

    def _build_generate_cmd(
        self, notebook_id: str, difficulty: str, quantity: str, instructions: str
    ) -> list[str]:
        cmd = [
            "generate",
            "flashcards",
            "--notebook",
            notebook_id,
            "--difficulty",
            difficulty,
            "--quantity",
            quantity,
            "--json",
        ]
        if instructions:
            cmd.append(instructions)
        return cmd

    def _needs_retry(self, stderr: str) -> bool:
        return "GENERATION_FAILED" in stderr or "rate limit" in stderr.lower()

    def _extract_artifact_id(self, data: dict) -> str | None:
        return data.get("task_id") or data.get("artifact_id") or data.get("id")

    def _log_command_output(self, stdout: str, stderr: str, prefix: str = "") -> None:
        label = f"{prefix} " if prefix else ""
        logger.debug(f"{label}stdout: {stdout[:500] if stdout else 'empty'}")
        logger.debug(f"{label}stderr: {stderr[:500] if stderr else 'empty'}")

    def _perform_retry(self, cmd: list[str]) -> tuple[str, str]:
        logger.warning(
            "Rate limit ou falha na geração, aguardando 5 minutos para retry..."
        )
        time.sleep(300)
        _, stdout, stderr = self._run(cmd)
        self._log_command_output(stdout, stderr, "Retry")
        return stdout, stderr

    def _execute_with_retry(self, cmd: list[str]) -> tuple[str, str]:
        _, stdout, stderr = self._run(cmd, check=False)
        self._log_command_output(stdout, stderr)

        if self._needs_retry(stderr):
            return self._perform_retry(cmd)

        return stdout, stderr

    def generate_flashcards(
        self,
        notebook_id: str,
        difficulty: str = "medium",
        quantity: str = "standard",
        instructions: str = "",
    ) -> str | None:
        cmd = self._build_generate_cmd(notebook_id, difficulty, quantity, instructions)

        try:
            stdout, _ = self._execute_with_retry(cmd)
            data = json.loads(stdout)
            artifact_id = self._extract_artifact_id(data)
            logger.debug(f"Extracted artifact_id: {artifact_id}")
            return artifact_id
        except Exception as e:
            logger.error(f"Erro ao gerar flashcards: {e}")
            return None

    def wait_for_artifact(
        self, notebook_id: str, artifact_id: str, timeout: int = 900
    ) -> bool:
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
        except Exception as e:
            logger.error(f"Erro ao baixar flashcards: {e}")
            return False

    def _extract_cards_data(self, data: dict | list) -> list:
        if isinstance(data, list):
            return data
        return data.get("cards", data.get("flashcards", []))

    def _create_flashcard(self, item: dict) -> Flashcard | None:
        front = item.get("front", item.get("question", item.get("q", "")))
        back = item.get("back", item.get("answer", item.get("a", "")))
        if front and back:
            return Flashcard(front=front, back=back)
        return None

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        flashcards = []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            cards_data = self._extract_cards_data(data)

            for item in cards_data:
                card = self._create_flashcard(item)
                if card:
                    flashcards.append(card)
        except Exception as e:
            logger.error(f"Erro ao fazer parse dos flashcards: {e}")
        return flashcards

    def delete_notebook(self, notebook_id: str) -> bool:
        try:
            self._run(["notebook", "delete", notebook_id, "--force"], check=False)
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar notebook: {e}")
            return False
