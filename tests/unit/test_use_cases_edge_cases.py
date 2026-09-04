"""Tests for uncovered lines in use_cases.py (100% coverage)."""

from pathlib import Path
from unittest.mock import MagicMock

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import (
    GenerateFlashcardsUseCase,
)
from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.infrastructure.chunk_state_repository import (
    FileSystemChunkStateRepository,
)


class TestSafePdfPathEdgeCases:
    """Test _is_safe_file_path edge cases for 100% coverage."""

    def test_is_safe_file_path_rejects_symlink(self, tmp_path, mock_generator):
        """Test that symlinks are rejected (lines 195-196)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        # Create a real PDF file
        real_pdf = input_dir / "real.pdf"
        real_pdf.write_text("PDF content")

        # Create a symlink to the PDF
        symlink_pdf = input_dir / "symlink.pdf"
        symlink_pdf.symlink_to(real_pdf)

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        # Symlink should be rejected
        result = use_case._is_safe_file_path(symlink_pdf, input_dir)
        assert result is False

    def test_corrupt_pdf_does_not_leave_resume_lock(
        self, tmp_path, mock_generator
    ):
        """Non-chunked failures must not publish a resume lock artifact."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "corrupt.pdf").write_bytes(b"not a PDF")

        use_case = GenerateFlashcardsUseCase(
            generator=mock_generator(should_fail_source=True),
            chunk_state_repository=FileSystemChunkStateRepository(),
        )
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        assert use_case.execute(request) == []
        assert use_case.last_run_had_errors is True
        assert not (
            output_dir / ".flashcards_resume" / ".corrupt.lock"
        ).exists()

    def test_oversized_pdf_fails_closed_without_provider_call(
        self, tmp_path, mock_generator, monkeypatch
    ):
        """Resource-limit failures must not reach the provider boundary."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "oversized.pdf").write_bytes(b"012345")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)
        monkeypatch.setattr(use_case.pdf_chunker, "MAX_PDF_FILE_BYTES", 5)
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        assert use_case.execute(request) == []
        assert use_case.last_run_had_errors is True
        assert generator._notebooks == {}

    def test_is_safe_file_path_rejects_non_pdf(self, tmp_path, mock_generator):
        """Test that non-PDF files are rejected (lines 216-217)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        # Create a text file (not PDF)
        text_file = input_dir / "document.txt"
        text_file.write_text("Not a PDF")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        # Non-PDF should be rejected
        result = use_case._is_safe_file_path(text_file, input_dir)
        assert result is False

    def test_is_safe_file_path_outside_directory(
        self, tmp_path, mock_generator
    ):
        """Test that paths outside input directory are rejected (lines 205-207)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        # Create a PDF in another directory
        other_pdf = other_dir / "other.pdf"
        other_pdf.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        # PDF outside input directory should be rejected
        result = use_case._is_safe_file_path(other_pdf, input_dir)
        assert result is False

    def test_snapshot_source_rejects_symlinked_snapshot_directory(
        self, tmp_path, mock_generator
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        pdf_path = input_dir / "source.pdf"
        pdf_path.write_bytes(b"PDF content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        pdf_output_path = output_dir / "source"
        pdf_output_path.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (pdf_output_path / ".flashcards_sources").symlink_to(
            outside_dir, target_is_directory=True
        )

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        assert use_case._snapshot_source(pdf_path, pdf_output_path) is None
        assert list(outside_dir.iterdir()) == []

    def test_snapshot_source_copy_failure_does_not_mask_original_error(
        self, tmp_path, mock_generator, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        pdf_path = input_dir / "source.pdf"
        pdf_path.write_bytes(b"PDF content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        pdf_output_path = output_dir / "source"
        pdf_output_path.mkdir()

        def fail_fsync(_fd: int) -> None:
            raise OSError("copy failed")

        monkeypatch.setattr(
            "flashcards_generator.application.use_cases.os.fsync",
            fail_fsync,
        )
        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        assert use_case._snapshot_source(pdf_path, pdf_output_path) is None
        snapshot_dir = pdf_output_path / ".flashcards_sources"
        assert list(snapshot_dir.iterdir()) == []


class TestGetOutputSubdirEdgeCases:
    def test_get_output_subdir_empty_parts(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "test.pdf"
        pdf_file.write_text("PDF content")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir
        assert result.exists()


class TestProcessPdfRuntimeError:
    def test_process_pdf_runtime_error(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "test.pdf"
        pdf_file.write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
        use_case._create_notebook = MagicMock(
            side_effect=RuntimeError("Test error")
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(
            pdf_file, input_dir, output_dir, request
        )
        assert result is None

    def test_process_pdf_unexpected_error(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "test.pdf"
        pdf_file.write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
        use_case._create_notebook = MagicMock(
            side_effect=TypeError("Unexpected error")
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(
            pdf_file, input_dir, output_dir, request
        )
        assert result is None


class TestGenerationSafetyRegressions:
    def test_explicit_files_apply_discovery_safety_boundary(
        self, temp_dirs, mock_generator
    ) -> None:
        input_dir, output_dir = temp_dirs
        outside_pdf = input_dir.parent / "outside.pdf"
        outside_pdf.write_text("outside")
        (input_dir / "notes.txt").write_text("not a source")
        selected_pdf = input_dir / "selected.pdf"
        selected_pdf.write_text("selected")
        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            explicit_files=["../outside.pdf", "notes.txt", "selected.pdf"],
        )

        selected = use_case._find_all_pdfs(input_dir, request)

        assert selected == [selected_pdf.resolve()]
        assert not (output_dir.parent / "outside.csv").exists()

    def test_input_swap_after_discovery_is_not_processed(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir = temp_dirs
        source = input_dir / "a.pdf"
        source.write_text("trusted")
        outside = input_dir.parent / "outside.pdf"
        outside.write_text("untrusted")
        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        use_case._process_pdf = MagicMock(return_value=None)
        original_output_subdir = use_case._get_output_subdir

        def replace_after_discovery(
            pdf_path: Path, source_root: Path, result_root: Path
        ) -> Path:
            source.unlink()
            source.symlink_to(outside)
            return original_output_subdir(pdf_path, source_root, result_root)

        monkeypatch.setattr(
            use_case, "_get_output_subdir", replace_after_discovery
        )

        result = use_case.execute(
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir
            )
        )

        assert result == []
        use_case._process_pdf.assert_not_called()

    def test_background_generation_creates_no_completion_marker(
        self, temp_dirs, mock_generator
    ) -> None:
        input_dir, output_dir = temp_dirs
        source = input_dir / "file.pdf"
        source.write_text("source")
        use_case = GenerateFlashcardsUseCase(generator=mock_generator())
        use_case._process_pdf = MagicMock(
            return_value=Deck(name="file", description="generating")
        )
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            wait_for_completion=False,
        )

        use_case.execute(request)
        use_case.execute(request)

        assert not (output_dir / "file.csv").exists()
        assert use_case._process_pdf.call_count == 2

    def test_normal_generation_deduplicates_before_export(
        self, tmp_path, mock_generator
    ) -> None:
        card = Flashcard(
            front="The {{c1::same fact}} has sufficient context for study.",
            back="The explanation contains enough detail for a useful card.",
        )
        generator = mock_generator()
        generator.parse_flashcards = MagicMock(
            return_value=[card, card.model_copy()]
        )
        use_case = GenerateFlashcardsUseCase(generator=generator)

        deck = use_case._download_and_convert(
            "notebook",
            "artifact",
            tmp_path,
            "deck",
            "source",
        )

        assert len(deck.flashcards) == 1
