"""Lower-level client for the NotebookLM CLI."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.domain.exceptions import NotebookLMResponseError
from flashcards_generator.infrastructure.document_limits import (
    MAX_FLASHCARDS as DEFAULT_MAX_FLASHCARDS,
)
from flashcards_generator.infrastructure.document_limits import (
    MAX_JSON_BYTES as DEFAULT_MAX_JSON_BYTES,
)
from flashcards_generator.infrastructure.logging_config import get_logger

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

    @staticmethod
    def _response_error(
        operation: str, reason: str
    ) -> NotebookLMResponseError:
        return NotebookLMResponseError(operation, reason)

    def _parse_json(self, stdout: str, operation: str) -> Any:
        if len(stdout.encode("utf-8")) > self.MAX_JSON_BYTES:
            raise self._response_error(
                operation,
                f"JSON exceeds maximum size of {self.MAX_JSON_BYTES} bytes",
            )
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as error:
            raise self._response_error(operation, "invalid JSON") from error

    def _extract_identifier(
        self, data: Any, operation: str, *keys: str
    ) -> str:
        if not isinstance(data, dict):
            raise self._response_error(
                operation, "expected an object response"
            )
        for key in keys:
            identifier = self._identifier_value(data.get(key))
            if identifier is not None:
                return identifier
        raise self._response_error(operation, "missing nonempty identifier")

    @staticmethod
    def _identifier_value(value: Any) -> str | None:
        if isinstance(value, str):
            return value if value.strip() else None
        if isinstance(value, dict):
            nested_id = value.get("id")
            return (
                nested_id
                if isinstance(nested_id, str) and nested_id.strip()
                else None
            )
        return None

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
        except (OSError, RuntimeError, subprocess.SubprocessError):
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
        except (OSError, RuntimeError, subprocess.SubprocessError):
            logger.error("NotebookLM download failed")
            return False

    def _extract_cards_data(self, data: Any) -> list[Any]:
        if isinstance(data, list):
            self._validate_card_count(data)
            return data
        if not isinstance(data, dict):
            raise self._response_error(
                "parse flashcards", "expected an array or object"
            )
        for key in ("cards", "flashcards"):
            if key in data:
                cards = data[key]
                if isinstance(cards, list):
                    self._validate_card_count(cards)
                    return cards
                raise self._response_error(
                    "parse flashcards", f"{key} must be an array"
                )
        raise self._response_error("parse flashcards", "missing cards array")

    def _validate_card_count(self, cards: list[Any]) -> None:
        if len(cards) > self.MAX_FLASHCARDS:
            raise self._response_error(
                "parse flashcards",
                f"card count exceeds maximum of {self.MAX_FLASHCARDS}",
            )

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
        try:
            if json_path.stat().st_size > self.MAX_JSON_BYTES:
                raise self._response_error(
                    "parse flashcards",
                    f"JSON exceeds maximum size of {self.MAX_JSON_BYTES} bytes",
                )
            data = self._parse_json(
                json_path.read_text(encoding="utf-8"), "parse flashcards"
            )
        except OSError as error:
            raise self._response_error(
                "parse flashcards", "unable to read file"
            ) from error

        flashcards = []
        for index, item in enumerate(self._extract_cards_data(data)):
            if not isinstance(item, dict):
                raise self._response_error(
                    "parse flashcards", f"card {index} must be an object"
                )
            card = self._create_flashcard(item)
            if card is None:
                raise self._response_error(
                    "parse flashcards",
                    f"card {index} has empty or non-string fields",
                )
            flashcards.append(card)
        return flashcards

    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete a notebook using the adapter's CLI dialect."""
        try:
            returncode, _, _ = self._run(
                ["delete", "-n", notebook_id, "-y"], check=False
            )
            return returncode == 0
        except (OSError, subprocess.SubprocessError):
            logger.warning("NotebookLM delete failed before completion")
            return False
