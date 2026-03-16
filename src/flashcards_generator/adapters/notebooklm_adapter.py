"""NotebookLM adapter implementing FlashcardGeneratorPort."""

from __future__ import annotations

import json
import subprocess
import time
from typing import TYPE_CHECKING, ClassVar

from flashcards_generator.domain.entities import Flashcard
from flashcards_generator.domain.exceptions import (
    ArtifactDownloadError,
    GenerationError,
    SourceProcessingError,
)
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    pass

logger = get_logger("notebooklm_adapter")

# Constants extracted from magic numbers
DEFAULT_COMMAND_TIMEOUT = 900
DEFAULT_SOURCE_TIMEOUT = 600
DEFAULT_ARTIFACT_TIMEOUT = 900
RATE_LIMIT_RETRY_DELAY_SECONDS = 300  # 5 minutes
MAX_LOG_OUTPUT_LENGTH = 500


class NotebookLMAdapter(FlashcardGeneratorPort):
    """Adapter for Google NotebookLM CLI."""

    # Error patterns for retry logic
    RATE_LIMIT_PATTERNS: ClassVar[list[str]] = ["GENERATION_FAILED", "rate limit"]

    def __init__(self, notebooklm_path: str, timeout: int = DEFAULT_COMMAND_TIMEOUT):
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout

    def _run_command(self, args: list[str], check: bool = True) -> tuple[int, str, str]:
        """Execute notebooklm CLI command."""
        cmd = [self.notebooklm_path, *args]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except KeyboardInterrupt:
            logger.info("Interrupted by user, terminating subprocess...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise
        if check and process.returncode != 0:
            raise RuntimeError(f"Command failed: {stderr}")
        return process.returncode, stdout, stderr

    def create_notebook(self, title: str) -> str:
        """Create a new notebook."""
        try:
            _, stdout, _ = self._run_command(["create", title, "--json"])
        except RuntimeError as e:
            raise GenerationError("", f"Failed to create notebook: {e}") from e
        data: dict = json.loads(stdout)
        notebook_id = data.get("id") or data.get("notebook", {}).get("id")
        if not notebook_id:
            raise GenerationError("", f"Failed to create notebook: {data}")
        return str(notebook_id)

    def add_source(self, notebook_id: str, pdf_path: Path) -> str:
        """Add PDF source to notebook."""
        cmd = ["source", "add", str(pdf_path), "--notebook", notebook_id, "--json"]
        _, stdout, _ = self._run_command(cmd)
        data: dict = json.loads(stdout)
        source_id = data.get("source_id") or data.get("source", {}).get("id")
        if not source_id:
            raise SourceProcessingError(pdf_path, f"Failed to add source: {data}")
        return str(source_id)

    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = DEFAULT_SOURCE_TIMEOUT
    ) -> bool:
        """Wait for source processing."""
        cmd = [
            "source",
            "wait",
            source_id,
            "-n",
            notebook_id,
            "--timeout",
            str(timeout),
        ]
        returncode, _, _ = self._run_command(cmd, check=False)
        return returncode == 0

    def _build_generate_command(
        self, notebook_id: str, config: GenerationConfig
    ) -> list[str]:
        """Build generate flashcards command."""
        cmd = [
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
            sanitized_instructions = config.instructions.replace("\n", " ").strip()
            cmd.append(sanitized_instructions)
        return cmd

    def _needs_retry(self, stderr: str) -> bool:
        """Check if error indicates rate limiting."""
        stderr_lower = stderr.lower()
        return any(
            pattern.lower() in stderr_lower for pattern in self.RATE_LIMIT_PATTERNS
        )

    def _extract_artifact_id(self, data: dict) -> str | None:
        """Extract artifact ID from response."""
        return data.get("task_id") or data.get("artifact_id") or data.get("id")

    def _log_command_output(self, stdout: str, stderr: str, prefix: str = "") -> None:
        """Log command output (truncated)."""
        label = f"{prefix} " if prefix else ""
        truncated_stdout = stdout[:MAX_LOG_OUTPUT_LENGTH] if stdout else "empty"
        truncated_stderr = stderr[:MAX_LOG_OUTPUT_LENGTH] if stderr else "empty"
        logger.debug(f"{label}stdout: {truncated_stdout}")
        logger.debug(f"{label}stderr: {truncated_stderr}")

    def _perform_retry(self, cmd: list[str]) -> tuple[str, str]:
        """Wait and retry after rate limit."""
        logger.warning(
            f"Rate limit or generation failure, waiting "
            f"{RATE_LIMIT_RETRY_DELAY_SECONDS}s for retry..."
        )
        time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
        _, stdout, stderr = self._run_command(cmd)
        self._log_command_output(stdout, stderr, "Retry")
        return stdout, stderr

    def _execute_with_retry(self, cmd: list[str]) -> tuple[str, str]:
        """Execute command with retry on rate limit."""
        returncode, stdout, stderr = self._run_command(cmd, check=False)
        self._log_command_output(stdout, stderr)

        if self._needs_retry(stderr):
            return self._perform_retry(cmd)

        if returncode != 0:
            logger.error(f"Command failed with exit code {returncode}: {stderr}")

        return stdout, stderr

    def generate_flashcards(
        self, notebook_id: str, config: GenerationConfig
    ) -> str | None:
        """Generate flashcards with retry logic."""
        cmd = self._build_generate_command(notebook_id, config)

        try:
            stdout, stderr = self._execute_with_retry(cmd)
            if not stdout or stdout.strip() == "":
                logger.error(f"Generation returned empty output: {stderr}")
                return None
            data = json.loads(stdout)
            artifact_id = self._extract_artifact_id(data)
            logger.debug(f"Extracted artifact_id: {artifact_id}")
            return artifact_id
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse generation response: {e}")
            return None
        except subprocess.TimeoutExpired:
            logger.error("Generation command timed out")
            return None

    def wait_for_artifact(
        self,
        notebook_id: str,
        artifact_id: str,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> bool:
        """Wait for artifact generation."""
        cmd = [
            "artifact",
            "wait",
            artifact_id,
            "-n",
            notebook_id,
            "--timeout",
            str(timeout),
        ]
        returncode, _, _ = self._run_command(cmd, check=False)
        return returncode == 0

    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        """Download flashcards artifact."""
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
            self._run_command(cmd)
            return True
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            RuntimeError,
        ) as e:
            raise ArtifactDownloadError(artifact_id, str(e)) from e

    def _extract_cards_data(self, data: dict | list) -> list:
        """Extract cards list from various JSON structures."""
        if isinstance(data, list):
            return data
        cards: list = data.get("cards", data.get("flashcards", []))
        return cards

    def _create_flashcard(self, item: dict) -> Flashcard | None:
        """Create Flashcard from JSON item."""
        front = item.get("front", item.get("question", item.get("q", "")))
        back = item.get("back", item.get("answer", item.get("a", "")))
        if front and back:
            return Flashcard(front=front, back=back)
        return None

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        """Parse flashcards from JSON file."""
        flashcards: list[Flashcard] = []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            cards_data = self._extract_cards_data(data)

            for item in cards_data:
                card = self._create_flashcard(item)
                if card:
                    flashcards.append(card)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to parse flashcards from {json_path}: {e}")
        return flashcards

    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete notebook (best effort)."""
        try:
            logger.debug(f"Deleting notebook: {notebook_id[:8]}...")
            returncode, _, _ = self._run_command(
                ["notebook", "delete", notebook_id, "--force"], check=False
            )
            if returncode == 0:
                logger.info(f"Successfully deleted notebook: {notebook_id[:8]}...")
                return True
            else:
                logger.warning(f"Failed to delete notebook {notebook_id[:8]}...")
                return False
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(f"Failed to delete notebook {notebook_id}: {e}")
            return False
