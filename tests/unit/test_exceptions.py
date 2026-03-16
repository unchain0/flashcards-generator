from pathlib import Path

import pytest

from flashcards_generator.domain.exceptions import (
    ArtifactDownloadError,
    FlashcardsGeneratorError,
    GenerationError,
    NotebookCleanupError,
    SourceProcessingError,
)


class TestExceptions:
    def test_flashcards_generator_error(self):
        with pytest.raises(FlashcardsGeneratorError):
            raise FlashcardsGeneratorError("Test error")

    def test_source_processing_error(self):
        path = Path("/path/to/file.pdf")
        error = SourceProcessingError(path, "PDF corrupted")

        assert error.source_path == path
        assert error.reason == "PDF corrupted"
        assert "Failed to process" in str(error)
        assert "PDF corrupted" in str(error)

    def test_generation_error(self):
        error = GenerationError("nb123", "API timeout")

        assert error.notebook_id == "nb123"
        assert error.reason == "API timeout"
        assert "Generation failed" in str(error)
        assert "nb123" in str(error)

    def test_artifact_download_error(self):
        error = ArtifactDownloadError("art456", "Network error")

        assert error.artifact_id == "art456"
        assert error.reason == "Network error"
        assert "Download failed" in str(error)
        assert "art456" in str(error)

    def test_notebook_cleanup_error(self):
        error = NotebookCleanupError("nb789", "Permission denied")

        assert error.notebook_id == "nb789"
        assert error.reason == "Permission denied"
        assert "Cleanup failed" in str(error)
        assert "nb789" in str(error)
