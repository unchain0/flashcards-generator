"""NotebookLM adapter implementing FlashcardGeneratorPort."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.domain.exceptions import (
    ArtifactDownloadError,
    GenerationError,
    NotebookLMResponseError,
    SourceProcessingError,
)
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from flashcards_generator.infrastructure.document_limits import (
    MAX_FLASHCARDS as DEFAULT_MAX_FLASHCARDS,
)
from flashcards_generator.infrastructure.document_limits import (
    MAX_JSON_BYTES as DEFAULT_MAX_JSON_BYTES,
)
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path


logger = get_logger("notebooklm_adapter")

DEFAULT_COMMAND_TIMEOUT = 900
DEFAULT_SOURCE_TIMEOUT = 600
DEFAULT_ARTIFACT_TIMEOUT = 900
PROCESS_CLEANUP_TIMEOUT = 5
RATE_LIMIT_RETRY_DELAY_SECONDS = 300
DOWNLOAD_RETRY_DELAY_SECONDS = 30
MAX_DOWNLOAD_RETRIES = 3


class NotebookLMAdapter(FlashcardGeneratorPort):
    """Adapter for the NotebookLM CLI using a list-argv process contract."""

    MAX_JSON_BYTES = DEFAULT_MAX_JSON_BYTES
    MAX_FLASHCARDS = DEFAULT_MAX_FLASHCARDS

    TRANSIENT_ERROR_PATTERNS: ClassVar[tuple[str, ...]] = (
        "rate limit",
        "too many requests",
        "temporarily unavailable",
        "rpc create_artifact failed",
    )

    def __init__(
        self, notebooklm_path: str, timeout: int = DEFAULT_COMMAND_TIMEOUT
    ):
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout

    def _run_command(
        self,
        args: list[str],
        check: bool = True,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        """Run one CLI command and reap it on timeout or cancellation."""
        command_timeout = self.timeout if timeout is None else timeout
        process = subprocess.Popen(
            [self.notebooklm_path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=False,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=command_timeout)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            self._stop_process(process)
            raise

        if check and process.returncode != 0:
            raise RuntimeError(
                self._command_failure(process.returncode, stderr)
            )
        return process.returncode, stdout, stderr

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        """Stop the command group where possible and always reap its leader."""
        self._signal_process(process, signal.SIGTERM)
        try:
            process.wait(timeout=PROCESS_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._signal_process(process, signal.SIGKILL)
            process.wait()

    def _signal_process(
        self, process: subprocess.Popen[str], signal_number: int
    ) -> None:
        """Signal the isolated process group, falling back to its leader."""
        pid = getattr(process, "pid", None)
        if os.name == "posix" and isinstance(pid, int):
            try:
                os.killpg(pid, signal_number)
                return
            except (OSError, ProcessLookupError):
                pass
        if signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    @staticmethod
    def _command_failure(returncode: int, stderr: str) -> str:
        return (
            "NotebookLM command failed "
            f"(status={returncode}, stderr_chars={len(stderr)})"
        )

    @staticmethod
    def _response_error(
        operation: str, reason: str
    ) -> NotebookLMResponseError:
        return NotebookLMResponseError(operation, reason)

    def create_notebook(self, title: str) -> str:
        """Create a new notebook."""
        try:
            _, stdout, _ = self._run_command(["create", title, "--json"])
            data = self._parse_json(stdout, "create notebook")
            notebook_id = self._extract_identifier(
                data, "create notebook", "id", "notebook"
            )
            return notebook_id
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            raise GenerationError("", str(error)) from error

    def add_source(self, notebook_id: str, pdf_path: Path) -> str:
        """Add a PDF source to notebook."""
        command = [
            "source",
            "add",
            str(pdf_path),
            "--notebook",
            notebook_id,
            "--json",
        ]
        try:
            _, stdout, _ = self._run_command(command)
            data = self._parse_json(stdout, "add source")
            return self._extract_identifier(
                data, "add source", "source_id", "source"
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            raise SourceProcessingError(pdf_path, str(error)) from error

    def wait_for_source(
        self,
        notebook_id: str,
        source_id: str,
        timeout: int = DEFAULT_SOURCE_TIMEOUT,
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
        returncode, _, _ = self._run_command(
            command, check=False, timeout=timeout
        )
        return returncode == 0

    def _build_generate_command(
        self, notebook_id: str, config: GenerationConfig
    ) -> list[str]:
        """Build the selected generate flashcards CLI dialect."""
        command = [
            "generate",
            "flashcards",
            "--notebook",
            notebook_id,
            "--difficulty",
            config.difficulty,
            "--quantity",
            config.quantity,
            "--json",
        ]
        if config.instructions:
            command.append(config.instructions.replace("\n", " ").strip())
        return command

    def _needs_retry(self, stderr: str) -> bool:
        """Return whether a failed command reported a transient condition."""
        stderr_lower = stderr.lower()
        return any(
            pattern in stderr_lower
            for pattern in self.TRANSIENT_ERROR_PATTERNS
        )

    def _log_command_result(
        self,
        command: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
        attempt: int,
        timeout: int,
    ) -> None:
        """Log metadata without exposing CLI output, prompts, or credentials."""
        logger.debug(
            "NotebookLM command completed: "
            f"operation={command[0]} status={returncode} attempt={attempt} "
            f"timeout={timeout} stdout_chars={len(stdout)} "
            f"stderr_chars={len(stderr)}"
        )

    def _execute_with_retry(
        self, command: list[str], timeout: int
    ) -> tuple[int, str, str]:
        """Retry exactly one classified transient nonzero generation failure."""
        returncode, stdout, stderr = self._run_command(
            command, check=False, timeout=timeout
        )
        self._log_command_result(
            command, returncode, stdout, stderr, attempt=1, timeout=timeout
        )
        if returncode == 0 or not self._needs_retry(stderr):
            return returncode, stdout, stderr

        logger.warning(
            "NotebookLM generation transient failure; retrying once"
        )
        time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
        returncode, stdout, stderr = self._run_command(
            command, check=False, timeout=timeout
        )
        self._log_command_result(
            command, returncode, stdout, stderr, attempt=2, timeout=timeout
        )
        return returncode, stdout, stderr

    def generate_flashcards(
        self, notebook_id: str, config: GenerationConfig
    ) -> str | None:
        """Generate flashcards, returning ``None`` for optional failure."""
        command = self._build_generate_command(notebook_id, config)
        try:
            returncode, stdout, stderr = self._execute_with_retry(
                command, config.timeout_seconds
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.error("NotebookLM generation failed before completion")
            return None

        if returncode != 0:
            logger.error(
                "NotebookLM generation failed: "
                f"status={returncode} stderr_chars={len(stderr)}"
            )
            return None
        if not stdout.strip():
            logger.error("NotebookLM generation returned empty output")
            return None

        try:
            data = self._parse_json(stdout, "generate flashcards")
            return self._extract_identifier(
                data, "generate flashcards", "task_id", "artifact_id", "id"
            )
        except NotebookLMResponseError:
            logger.error("NotebookLM generation returned an invalid response")
            return None

    def wait_for_artifact(
        self,
        notebook_id: str,
        artifact_id: str,
        timeout: int = DEFAULT_ARTIFACT_TIMEOUT,
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
        returncode, _, _ = self._run_command(
            command, check=False, timeout=timeout
        )
        return returncode == 0

    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        """Download flashcards, retrying only transient nonzero failures."""
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
        for attempt in range(MAX_DOWNLOAD_RETRIES):
            try:
                returncode, _, stderr = self._run_command(command, check=False)
            except (OSError, subprocess.SubprocessError) as error:
                raise ArtifactDownloadError(artifact_id, str(error)) from error

            if returncode == 0:
                return True
            if (
                not self._needs_retry(stderr)
                or attempt == MAX_DOWNLOAD_RETRIES - 1
            ):
                raise ArtifactDownloadError(
                    artifact_id, self._command_failure(returncode, stderr)
                )
            logger.warning(
                "NotebookLM download transient failure: "
                f"attempt={attempt + 1} status={returncode}; retrying"
            )
            time.sleep(DOWNLOAD_RETRY_DELAY_SECONDS * (attempt + 1))

        raise AssertionError(
            "unreachable download retry state"
        )  # pragma: no cover

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

    def _extract_cards_data(self, data: Any) -> list[Any]:
        """Extract a schema-valid card array from a response envelope."""
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
        """Parse a downloaded card response or raise a contextual response error."""
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

    def delete_notebook(self, notebook_id: str, silent: bool = False) -> bool:
        """Delete a notebook using the selected CLI dialect."""
        try:
            returncode, _, stderr = self._run_command(
                ["delete", "-n", notebook_id, "-y"], check=False
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("NotebookLM delete failed before completion")
            return False
        if returncode == 0:
            if not silent:
                logger.info("NotebookLM notebook deleted")
            return True
        logger.warning(
            "NotebookLM delete failed: "
            f"status={returncode} stderr_chars={len(stderr)}"
        )
        return False

    def list_notebooks(self, days: int | None = None) -> list[dict[str, Any]]:
        """List notebooks, optionally filtering by creation date."""
        notebooks = self._load_notebooks()
        if days is None:
            return notebooks
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return [
            notebook
            for notebook in notebooks
            if self._created_on_or_after(notebook, cutoff)
        ]

    def _load_notebooks(self) -> list[dict[str, Any]]:
        data = self._list_notebook_data()
        notebooks = (
            data.get("notebooks", []) if isinstance(data, dict) else data
        )
        if not isinstance(notebooks, list):
            return []
        return [item for item in notebooks if isinstance(item, dict)]

    def _list_notebook_data(self) -> Any:
        try:
            returncode, stdout, stderr = self._run_command(
                ["list", "--json"], check=False
            )
            if returncode != 0:
                logger.error(
                    "NotebookLM list failed: "
                    f"status={returncode} stderr_chars={len(stderr)}"
                )
                return None
            return self._parse_json(stdout, "list notebooks")
        except (NotebookLMResponseError, OSError, subprocess.SubprocessError):
            logger.error("NotebookLM list returned an invalid response")
            return None

    def _created_on_or_after(
        self, notebook: dict[str, Any], cutoff: datetime
    ) -> bool:
        created = self._parse_datetime(
            notebook.get("created_at") or notebook.get("created")
        )
        return created is None or created >= cutoff

    def _parse_datetime(self, dt_str: Any) -> datetime | None:
        if not isinstance(dt_str, str):
            return None
        formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(dt_str, fmt).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
        return None

    def delete_all_notebooks(
        self, days: int | None = None, show_progress: bool = False
    ) -> tuple[int, int]:
        """Delete all notebooks. Returns ``(deleted_count, failed_count)``."""
        notebooks = self.list_notebooks(days=days)
        if not notebooks:
            logger.info("No notebooks found to delete")
            return 0, 0

        if show_progress:
            deleted, failed = self._delete_with_progress(notebooks)
        else:
            deleted, failed = self._delete_without_progress(notebooks)

        logger.info(f"Cleanup complete: {deleted} deleted, {failed} failed")
        return deleted, failed

    def _delete_with_progress(
        self, notebooks: list[dict[str, Any]]
    ) -> tuple[int, int]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            console=Console(),
        ) as progress:
            task = progress.add_task(
                f"Deleting {len(notebooks)} notebooks...",
                total=len(notebooks),
            )
            outcomes = []
            for notebook in notebooks:
                notebook_id = self._notebook_id(notebook)
                if notebook_id:
                    outcomes.append(
                        self.delete_notebook(notebook_id, silent=True)
                    )
                progress.update(task, advance=1)
        return outcomes.count(True), outcomes.count(False)

    def _delete_without_progress(
        self, notebooks: list[dict[str, Any]]
    ) -> tuple[int, int]:
        logger.info(f"Found {len(notebooks)} notebook(s) to delete...")
        outcomes = []
        for index, notebook in enumerate(notebooks, 1):
            notebook_id = self._notebook_id(notebook)
            if notebook_id:
                logger.info(f"[{index}/{len(notebooks)}] Deleting notebook...")
                outcomes.append(self.delete_notebook(notebook_id))
        return outcomes.count(True), outcomes.count(False)

    @staticmethod
    def _notebook_id(notebook: Any) -> str | None:
        value = notebook.get("id") if isinstance(notebook, dict) else notebook
        return value if isinstance(value, str) and value else None
