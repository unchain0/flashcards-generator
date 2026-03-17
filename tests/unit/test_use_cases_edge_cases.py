"""Tests for uncovered lines in use_cases.py (100% coverage)."""

from unittest.mock import MagicMock

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase


class TestSafePdfPathEdgeCases:
    """Test _is_safe_pdf_path edge cases for 100% coverage."""

    def test_is_safe_pdf_path_rejects_symlink(self, tmp_path, mock_generator):
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
        result = use_case._is_safe_pdf_path(symlink_pdf, input_dir)
        assert result is False

    def test_is_safe_pdf_path_rejects_non_pdf(self, tmp_path, mock_generator):
        """Test that non-PDF files are rejected (lines 216-217)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        # Create a text file (not PDF)
        text_file = input_dir / "document.txt"
        text_file.write_text("Not a PDF")

        use_case = GenerateFlashcardsUseCase(generator=mock_generator())

        # Non-PDF should be rejected
        result = use_case._is_safe_pdf_path(text_file, input_dir)
        assert result is False

    def test_is_safe_pdf_path_outside_directory(self, tmp_path, mock_generator):
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
        result = use_case._is_safe_pdf_path(other_pdf, input_dir)
        assert result is False


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
        use_case._create_notebook = MagicMock(side_effect=RuntimeError("Test error"))

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(pdf_file, input_dir, output_dir, request)
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
        use_case._create_notebook = MagicMock(side_effect=TypeError("Unexpected error"))

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(pdf_file, input_dir, output_dir, request)
        assert result is None
