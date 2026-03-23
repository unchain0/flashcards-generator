"""Domain-specific exceptions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class FlashcardsGeneratorError(Exception):
    """Base exception for all domain errors."""

    pass


class SourceProcessingError(FlashcardsGeneratorError):
    """Raised when PDF source cannot be processed."""

    def __init__(self, source_path: Path, reason: str) -> None:
        self.source_path = source_path
        self.reason = reason
        super().__init__(f"Failed to process {source_path}: {reason}")


class GenerationError(FlashcardsGeneratorError):
    """Raised when flashcard generation fails."""

    def __init__(self, notebook_id: str, reason: str) -> None:
        self.notebook_id = notebook_id
        self.reason = reason
        super().__init__(f"Generation failed for {notebook_id}: {reason}")


class ArtifactDownloadError(FlashcardsGeneratorError):
    """Raised when artifact cannot be downloaded."""

    def __init__(self, artifact_id: str, reason: str) -> None:
        self.artifact_id = artifact_id
        self.reason = reason
        super().__init__(f"Download failed for {artifact_id}: {reason}")


class NotebookCleanupError(FlashcardsGeneratorError):
    """Raised when notebook cleanup fails (non-critical)."""

    def __init__(self, notebook_id: str, reason: str) -> None:
        self.notebook_id = notebook_id
        self.reason = reason
        super().__init__(f"Cleanup failed for {notebook_id}: {reason}")


class CSVMergeError(FlashcardsGeneratorError):
    """Raised when CSV merge operation fails."""

    def __init__(self, folder_path: Path, reason: str) -> None:
        self.folder_path = folder_path
        self.reason = reason
        super().__init__(f"CSV merge failed for {folder_path}: {reason}")
