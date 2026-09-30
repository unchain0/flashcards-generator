"""NotebookLM adapter implementing FlashcardGeneratorPort."""

from __future__ import annotations

import os
import subprocess
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from flashcards_generator.domain_models.entities import Flashcard
from flashcards_generator.domain_models.exceptions import (
    ArtifactDownloadError,
    GenerationError,
    NotebookLMResponseError,
    SourceProcessingError,
)
from flashcards_generator.integrations.document_limits import (
    MAX_FLASHCARDS as DEFAULT_MAX_FLASHCARDS,
)
from flashcards_generator.integrations.document_limits import (
    MAX_JSON_BYTES as DEFAULT_MAX_JSON_BYTES,
)
from flashcards_generator.integrations.logging_config import get_logger
from flashcards_generator.integrations.notebooklm.catalog import (
    created_on_or_after,
    delete_notebooks,
    normalize_notebooks,
)
from flashcards_generator.integrations.notebooklm.process_runner import (
    NotebookLMProcessRunner,
)
from flashcards_generator.integrations.notebooklm.response_parser import (
    JSONValue,
    create_flashcard,
    extract_cards_data,
    extract_identifier,
    identifier_value,
    parse_flashcard_item,
    parse_json,
)
from flashcards_generator.services.ports.cancellation import CancellationPort
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)

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
        self,
        notebooklm_path: str,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
        *,
        profile: str | None = None,
        notebooklm_home: Path | None = None,
    ):
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout
        self.profile = profile
        self.notebooklm_home = notebooklm_home
        self._process_runner = NotebookLMProcessRunner(
            cleanup_timeout=PROCESS_CLEANUP_TIMEOUT
        )

    def cancel_active(self) -> None:
        self._process_runner.cancel_active()

    def cancellation_scope(
        self, token: CancellationPort | None
    ) -> AbstractContextManager[None]:
        return self._process_runner.cancellation_scope(token)

    def _wait_before_retry(self, timeout: float) -> None:
        self._process_runner.wait_before_retry(timeout)

    def _run_command(
        self,
        args: list[str],
        check: bool = True,
        timeout: int | None = None,
        *,
        cancellable: bool = True,
        cancel_on_token: bool = False,
        ignore_pre_cancel: bool = False,
        raise_on_cancel: bool = True,
    ) -> tuple[int, str, str]:
        command_timeout = self.timeout if timeout is None else timeout
        command = self._build_command(args)
        environment = self._command_environment()
        return self._process_runner.run_command(
            command,
            environment=environment,
            timeout=command_timeout,
            check=check,
            cancellable=cancellable,
            cancel_on_token=cancel_on_token,
            ignore_pre_cancel=ignore_pre_cancel,
            raise_on_cancel=raise_on_cancel,
        )

    def _build_command(self, args: list[str]) -> list[str]:
        command = [self.notebooklm_path]
        if self.profile is not None:
            command.extend(["--profile", self.profile])
        return [*command, *args]

    def _command_environment(self) -> dict[str, str] | None:
        if self.notebooklm_home is None:
            return None
        environment = os.environ.copy()
        environment["NOTEBOOKLM_HOME"] = str(self.notebooklm_home)
        return environment

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
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
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
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
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
        self._wait_before_retry(RATE_LIMIT_RETRY_DELAY_SECONDS)
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
        except OSError, subprocess.SubprocessError:
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
            returncode, stderr = self._attempt_download(command, artifact_id)
            if returncode == 0:
                return True
            if self._download_should_fail(stderr, attempt):
                raise ArtifactDownloadError(
                    artifact_id,
                    self._process_runner.command_failure(returncode, stderr),
                )
            logger.warning(
                "NotebookLM download transient failure: "
                f"attempt={attempt + 1} status={returncode}; retrying"
            )
            self._wait_before_retry(
                DOWNLOAD_RETRY_DELAY_SECONDS * (attempt + 1)
            )

        raise AssertionError(
            "unreachable download retry state"
        )  # pragma: no cover

    def _attempt_download(
        self, command: list[str], artifact_id: str
    ) -> tuple[int, str]:
        try:
            returncode, _, stderr = self._run_command(command, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise ArtifactDownloadError(artifact_id, str(error)) from error
        return returncode, stderr

    def _download_should_fail(self, stderr: str, attempt: int) -> bool:
        return (
            not self._needs_retry(stderr)
            or attempt == MAX_DOWNLOAD_RETRIES - 1
        )

    def _parse_json(self, stdout: str, operation: str) -> JSONValue:
        return parse_json(stdout, operation, self.MAX_JSON_BYTES)

    def _extract_identifier(
        self, data: JSONValue, operation: str, *keys: str
    ) -> str:
        return extract_identifier(data, operation, keys)

    @staticmethod
    def _identifier_value(value: JSONValue) -> str | None:
        return identifier_value(value)

    def _extract_cards_data(self, data: JSONValue) -> list[JSONValue]:
        return extract_cards_data(data, self.MAX_FLASHCARDS)

    def _create_flashcard(
        self, item: dict[str, JSONValue]
    ) -> Flashcard | None:
        return create_flashcard(item)

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        """Parse a downloaded card response or raise a contextual response error."""
        data = self._read_flashcard_json(json_path)
        return [
            self._parse_flashcard_item(item, index)
            for index, item in enumerate(self._extract_cards_data(data))
        ]

    def _read_flashcard_json(self, json_path: Path) -> JSONValue:
        try:
            if json_path.stat().st_size > self.MAX_JSON_BYTES:
                raise self._response_error(
                    "parse flashcards",
                    f"JSON exceeds maximum size of {self.MAX_JSON_BYTES} bytes",
                )
            data = self._parse_json(
                json_path.read_text(encoding="utf-8"), "parse flashcards"
            )
            return data
        except OSError as error:
            raise self._response_error(
                "parse flashcards", "unable to read file"
            ) from error

    def _parse_flashcard_item(self, item: JSONValue, index: int) -> Flashcard:
        return parse_flashcard_item(item, index)

    def delete_notebook(self, notebook_id: str, silent: bool = False) -> bool:
        """Delete a notebook using the selected CLI dialect."""
        try:
            returncode, _, stderr = self._run_command(
                ["delete", "-n", notebook_id, "-y"],
                check=False,
                cancellable=False,
                cancel_on_token=True,
                ignore_pre_cancel=True,
                raise_on_cancel=False,
            )
        except OSError, subprocess.SubprocessError:
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
        notebooks = normalize_notebooks(self._list_notebook_data())
        if days is None:
            return notebooks
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return [
            notebook
            for notebook in notebooks
            if created_on_or_after(notebook, cutoff)
        ]

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
        except NotebookLMResponseError, OSError, subprocess.SubprocessError:
            logger.error("NotebookLM list returned an invalid response")
            return None

    def delete_all_notebooks(
        self, days: int | None = None, show_progress: bool = False
    ) -> tuple[int, int]:
        """Delete all notebooks. Returns ``(deleted_count, failed_count)``."""
        notebooks = self.list_notebooks(days=days)
        if not notebooks:
            logger.info("No notebooks found to delete")
            return 0, 0

        def delete_notebook_with_mode(notebook_id: str, silent: bool) -> bool:
            return self.delete_notebook(notebook_id, silent=silent)

        deleted, failed = delete_notebooks(
            notebooks, delete_notebook_with_mode, show_progress, logger
        )

        logger.info(f"Cleanup complete: {deleted} deleted, {failed} failed")
        return deleted, failed
