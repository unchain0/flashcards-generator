"""Lower-level client for the NotebookLM CLI."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

from flashcards_generator.domain_models.entities import Flashcard
from flashcards_generator.domain_models.exceptions import (
    NotebookLMResponseError,
)
from flashcards_generator.integrations.document_limits import (
    MAX_FLASHCARDS as DEFAULT_MAX_FLASHCARDS,
)
from flashcards_generator.integrations.document_limits import (
    MAX_JSON_BYTES as DEFAULT_MAX_JSON_BYTES,
)
from flashcards_generator.integrations.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path


logger = get_logger("notebooklm_client")


class NotebookLMClient:
    """Small NotebookLM CLI helper using the adapter's argv/status contract."""

    MAX_JSON_BYTES = DEFAULT_MAX_JSON_BYTES
    MAX_FLASHCARDS = DEFAULT_MAX_FLASHCARDS

    def __init__(self, notebooklm_path: str, timeout: int = 900):
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout

    def _run(
        self,
        args: list[str],
        check: bool = True,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        """Execute one CLI command with a real subprocess deadline."""
        result = subprocess.run(
            [self.notebooklm_path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout if timeout is None else timeout,
            check=False,
            shell=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                "NotebookLM command failed "
                f"(status={result.returncode}, stderr_chars={len(result.stderr)})"
            )
        return result.returncode, result.stdout, result.stderr

    def _parse_json(self, stdout: str, operation: str) -> Any:
        if len(stdout.encode("utf-8")) > self.MAX_JSON_BYTES:
            raise NotebookLMResponseError(
                operation,
                f"JSON exceeds maximum size of {self.MAX_JSON_BYTES} bytes",
            )
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as error:
            raise NotebookLMResponseError(operation, "invalid JSON") from error

    def _extract_identifier(
        self, data: Any, operation: str, *keys: str
    ) -> str:
        if not isinstance(data, dict):
            raise NotebookLMResponseError(
                operation, "expected an object response"
            )
        for key in keys:
            identifier = self._identifier_value(data.get(key))
            if identifier is not None:
                return identifier
        raise NotebookLMResponseError(operation, "missing nonempty identifier")

    @staticmethod
    def _identifier_value(value: Any) -> str | None:
        if isinstance(value, dict):
            value = value.get("id")
        return value if isinstance(value, str) and value.strip() else None

    def create_notebook(self, title: str) -> str:
        """Create a new notebook."""
        _, stdout, _ = self._run(["create", title, "--json"])
        return self._extract_identifier(
            self._parse_json(stdout, "create notebook"),
            "create notebook",
            "id",
            "notebook",
        )

    def add_source(self, notebook_id: str, file_path: Path) -> str:
        """Add a source file to a notebook."""
        command = [
            "source",
            "add",
            str(file_path),
            "--notebook",
            notebook_id,
            "--json",
        ]
        _, stdout, _ = self._run(command)
        return self._extract_identifier(
            self._parse_json(stdout, "add source"),
            "add source",
            "source_id",
            "source",
        )

    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = 600
    ) -> bool:
        """Wait for source processing within the requested deadline."""
        command = [
            "source",
            "wait",
            source_id,
            "-n",
            notebook_id,
            "--timeout",
            str(timeout),
        ]
        returncode, _, _ = self._run(command, check=False, timeout=timeout)
        return returncode == 0

    def generate_flashcards(
        self,
        notebook_id: str,
        prompt: str,
        difficulty: str = "medium",
        quantity: str = "standard",
    ) -> str | None:
        """Generate flashcards; this convenience method is best effort."""
        command = [
            "generate",
            "flashcards",
            "--notebook",
            notebook_id,
            "--difficulty",
            difficulty,
            "--quantity",
            quantity,
            "--json",
            prompt.replace("\n", " ").strip(),
        ]
        try:
            _, stdout, _ = self._run(command)
            return self._extract_identifier(
                self._parse_json(stdout, "generate flashcards"),
                "generate flashcards",
                "task_id",
                "artifact_id",
                "id",
            )
        except OSError, RuntimeError, subprocess.SubprocessError:
            return None

    def wait_for_artifact(
        self, notebook_id: str, artifact_id: str, timeout: int = 900
    ) -> bool:
        """Wait for artifact generation within the requested deadline."""
        command = [
            "artifact",
            "wait",
            artifact_id,
            "-n",
            notebook_id,
            "--timeout",
            str(timeout),
        ]
        returncode, _, _ = self._run(command, check=False, timeout=timeout)
        return returncode == 0

    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        """Download a flashcards artifact to a file."""
        command = [
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
            self._run(command)
            return True
        except OSError, RuntimeError, subprocess.SubprocessError:
            logger.error("NotebookLM download failed")
            return False

    def _extract_cards_data(self, data: Any) -> list[Any]:
        if isinstance(data, list):
            return self._validated_card_array(data, "cards")
        if not isinstance(data, dict):
            raise NotebookLMResponseError(
                "parse flashcards", "expected an array or object"
            )
        return self._extract_card_envelope(data)

    def _extract_card_envelope(self, data: dict[str, Any]) -> list[Any]:
        for key in ("cards", "flashcards"):
            if key in data:
                return self._validated_card_array(data[key], key)
        raise NotebookLMResponseError(
            "parse flashcards", "missing cards array"
        )

    def _validated_card_array(self, cards: Any, key: str) -> list[Any]:
        if not isinstance(cards, list):
            raise NotebookLMResponseError(
                "parse flashcards", f"{key} must be an array"
            )
        if len(cards) > self.MAX_FLASHCARDS:
            raise NotebookLMResponseError(
                "parse flashcards",
                f"card count exceeds maximum of {self.MAX_FLASHCARDS}",
            )
        return cards

    def _create_flashcard(self, item: dict[str, Any]) -> Flashcard | None:
        """Build a card when both fields are nonempty strings."""
        front = item.get("front", item.get("question", item.get("q", "")))
        back = item.get("back", item.get("answer", item.get("a", "")))
        if (
            isinstance(front, str)
            and front.strip()
            and isinstance(back, str)
            and back.strip()
        ):
            return Flashcard(front=front, back=back)
        return None

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        """Parse valid card JSON or raise a contextual response error."""
        data = self._read_flashcard_json(json_path)
        return [
            self._parse_flashcard_item(item, index)
            for index, item in enumerate(self._extract_cards_data(data))
        ]

    def _read_flashcard_json(self, json_path: Path) -> Any:
        try:
            if json_path.stat().st_size > self.MAX_JSON_BYTES:
                raise NotebookLMResponseError(
                    "parse flashcards",
                    f"JSON exceeds maximum size of {self.MAX_JSON_BYTES} bytes",
                )
            return self._parse_json(
                json_path.read_text(encoding="utf-8"), "parse flashcards"
            )
        except OSError as error:
            raise NotebookLMResponseError(
                "parse flashcards", "unable to read file"
            ) from error

    def _parse_flashcard_item(self, item: Any, index: int) -> Flashcard:
        if not isinstance(item, dict):
            raise NotebookLMResponseError(
                "parse flashcards", f"card {index} must be an object"
            )
        card = self._create_flashcard(item)
        if card is None:
            raise NotebookLMResponseError(
                "parse flashcards",
                f"card {index} has empty or non-string fields",
            )
        return card

    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete a notebook using the adapter's CLI dialect."""
        try:
            returncode, _, _ = self._run(
                ["delete", "-n", notebook_id, "-y"], check=False
            )
            return returncode == 0
        except OSError, subprocess.SubprocessError:
            logger.warning("NotebookLM delete failed before completion")
            return False
