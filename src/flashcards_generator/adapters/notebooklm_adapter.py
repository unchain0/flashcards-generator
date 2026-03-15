"""NotebookLM adapter implementing FlashcardGeneratorPort."""
import json
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

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
    RATE_LIMIT_PATTERNS = ["GENERATION_FAILED", "rate limit"]
    
    def __init__(self, notebooklm_path: str, timeout: int = DEFAULT_COMMAND_TIMEOUT):
        self.notebooklm_path = notebooklm_path
        self.timeout = timeout
    
    def _run_command(
        self, args: List[str], check: bool = True
    ) -> Tuple[int, str, str]:
        """Execute notebooklm CLI command."""
        cmd = [self.notebooklm_path] + args
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed: {result.stderr}")
        return result.returncode, result.stdout, result.stderr
    
    def create_notebook(self, title: str) -> str:
        """Create a new notebook."""
        _, stdout, _ = self._run_command(["create", title, "--json"])
        data = json.loads(stdout)
        notebook_id = data.get("id") or data.get("notebook", {}).get("id")
        if not notebook_id:
            raise GenerationError("", f"Failed to create notebook: {data}")
        return notebook_id
    
    def add_source(self, notebook_id: str, pdf_path: Path) -> str:
        """Add PDF source to notebook."""
        cmd = [
            "source", "add", str(pdf_path),
            "--notebook", notebook_id, "--json"
        ]
        _, stdout, _ = self._run_command(cmd)
        data = json.loads(stdout)
        source_id = data.get("source_id") or data.get("source", {}).get("id")
        if not source_id:
            raise SourceProcessingError(
                pdf_path, f"Failed to add source: {data}"
            )
        return source_id
    
    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = DEFAULT_SOURCE_TIMEOUT
    ) -> bool:
        """Wait for source processing."""
        cmd = [
            "source", "wait", source_id,
            "-n", notebook_id,
            "--timeout", str(timeout),
        ]
        returncode, _, _ = self._run_command(cmd, check=False)
        return returncode == 0
    
    def _build_generate_command(
        self, notebook_id: str, config: GenerationConfig
    ) -> List[str]:
        """Build generate flashcards command."""
        cmd = [
            "generate", "flashcards",
            "--notebook", notebook_id,
            "--difficulty", config.difficulty,
            "--quantity", config.quantity,
            "--json",
        ]
        if config.instructions:
            cmd.append(config.instructions)
        return cmd
    
    def _needs_retry(self, stderr: str) -> bool:
        """Check if error indicates rate limiting."""
        stderr_lower = stderr.lower()
        return any(pattern.lower() in stderr_lower for pattern in self.RATE_LIMIT_PATTERNS)
    
    def _extract_artifact_id(self, data: dict) -> Optional[str]:
        """Extract artifact ID from response."""
        return data.get("task_id") or data.get("artifact_id") or data.get("id")
    
    def _log_command_output(self, stdout: str, stderr: str, prefix: str = "") -> None:
        """Log command output (truncated)."""
        label = f"{prefix} " if prefix else ""
        truncated_stdout = stdout[:MAX_LOG_OUTPUT_LENGTH] if stdout else "empty"
        truncated_stderr = stderr[:MAX_LOG_OUTPUT_LENGTH] if stderr else "empty"
        logger.debug(f"{label}stdout: {truncated_stdout}")
        logger.debug(f"{label}stderr: {truncated_stderr}")
    
    def _perform_retry(self, cmd: List[str]) -> Tuple[str, str]:
        """Wait and retry after rate limit."""
        logger.warning(
            f"Rate limit or generation failure, waiting {RATE_LIMIT_RETRY_DELAY_SECONDS}s for retry..."
        )
        time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
        _, stdout, stderr = self._run_command(cmd)
        self._log_command_output(stdout, stderr, "Retry")
        return stdout, stderr
    
    def _execute_with_retry(self, cmd: List[str]) -> Tuple[str, str]:
        """Execute command with retry on rate limit."""
        _, stdout, stderr = self._run_command(cmd, check=False)
        self._log_command_output(stdout, stderr)
        
        if self._needs_retry(stderr):
            return self._perform_retry(cmd)
        
        return stdout, stderr
    
    def generate_flashcards(
        self, notebook_id: str, config: GenerationConfig
    ) -> Optional[str]:
        """Generate flashcards with retry logic."""
        cmd = self._build_generate_command(notebook_id, config)
        
        try:
            stdout, _ = self._execute_with_retry(cmd)
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
        self, notebook_id: str, artifact_id: str, timeout: int = DEFAULT_ARTIFACT_TIMEOUT
    ) -> bool:
        """Wait for artifact generation."""
        cmd = [
            "artifact", "wait", artifact_id,
            "-n", notebook_id,
            "--timeout", str(timeout),
        ]
        returncode, _, _ = self._run_command(cmd, check=False)
        return returncode == 0
    
    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        """Download flashcards artifact."""
        cmd = [
            "download", "flashcards",
            "-n", notebook_id,
            "-a", artifact_id,
            "--format", "json",
            str(output_path),
        ]
        try:
            self._run_command(cmd)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise ArtifactDownloadError(artifact_id, str(e))
    
    def _extract_cards_data(self, data: dict | list) -> list:
        """Extract cards list from various JSON structures."""
        if isinstance(data, list):
            return data
        return data.get("cards", data.get("flashcards", []))
    
    def _create_flashcard(self, item: dict) -> Optional[Flashcard]:
        """Create Flashcard from JSON item."""
        front = item.get("front", item.get("question", item.get("q", "")))
        back = item.get("back", item.get("answer", item.get("a", "")))
        if front and back:
            return Flashcard(front=front, back=back)
        return None
    
    def parse_flashcards(self, json_path: Path) -> List[Flashcard]:
        """Parse flashcards from JSON file."""
        flashcards: List[Flashcard] = []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            cards_data = self._extract_cards_data(data)
            
            for item in cards_data:
                card = self._create_flashcard(item)
                if card:
                    flashcards.append(card)
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            logger.error(f"Failed to parse flashcards from {json_path}: {e}")
        return flashcards
    
    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete notebook (best effort)."""
        try:
            self._run_command(
                ["notebook", "delete", notebook_id, "--force"],
                check=False
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to delete notebook {notebook_id}: {e}")
            return False
