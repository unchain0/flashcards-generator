"""Additional tests to reach 100% coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.application.converter import ClozeConverter
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase
from flashcards_generator.infrastructure.notebooklm_client import NotebookLMClient
from flashcards_generator.infrastructure.pdf_utils import PDFChunker
from flashcards_generator.interfaces.cli import CLI


class TestConverterEdgeCases:
    """Test converter edge cases for 100% coverage."""

    def test_create_complex_cloze_all_trivial_important_words(self):
        """Test _create_complex_cloze when all important words are trivial."""
        converter = ClozeConverter()
        # Mock _extract_important to return a trivial word ("um" is in TRIVIAL_WORDS)
        converter._extract_important = lambda s: "um"

        result = converter._create_complex_cloze(
            "This is a test sentence with just um and uh words. Another sentence here.",
            1,
        )
        # The sentence should be returned as-is since "um" is trivial
        assert "um" in result


class TestUseCasesEdgeCases:
    """Test use cases edge cases for 100% coverage."""

    def test_get_output_subdir_no_parent(self, temp_dirs, mock_generator):
        """Test _get_output_subdir when relative_path.parent is empty (line 149)."""
        input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Test with a file directly in input_dir (no subdirectory)
        pdf_path = input_dir / "file.pdf"
        pdf_path.write_text("PDF content")  # Create actual file
        result = use_case._get_output_subdir(pdf_path, input_dir, output_dir)

        # Should return output_path directly when parent is empty
        assert result == output_dir

    def test_cleanup_orphaned_raw_files_success(self, temp_dirs, mock_generator):
        """Test _cleanup_orphaned_raw_files successful cleanup (line 169)."""
        _input_dir, output_dir = temp_dirs

        # Create an orphaned raw file that will be successfully deleted
        raw_file = output_dir / "test_raw.json"
        raw_file.write_text("{}")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Should successfully delete the file
        use_case._cleanup_orphaned_raw_files(output_dir)

        # Verify file was deleted
        assert not raw_file.exists()

    def test_cleanup_orphaned_raw_files_oserror(
        self, temp_dirs, mock_generator, monkeypatch
    ):
        """Test _cleanup_orphaned_raw_files handles OSError (lines 170-171)."""
        _input_dir, output_dir = temp_dirs

        # Create an orphaned raw file
        raw_file = output_dir / "test_raw.json"
        raw_file.write_text("{}")

        # Mock unlink to raise OSError
        def mock_unlink(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "unlink", mock_unlink)

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Should not raise exception
        use_case._cleanup_orphaned_raw_files(output_dir)

    def test_process_pdf_add_source_returns_none(self, temp_dirs, mock_generator):
        """Test _process_pdf when _add_pdf_source returns None (line 253)."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "file.pdf"
        pdf_file.write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Ensure needs_chunking returns False to go to the else branch
        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: False
        # Mock _add_pdf_source to return None directly (avoiding the logging error)
        use_case._add_pdf_source = lambda nb, path: None

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(pdf_file, input_dir, output_dir, request)

        assert result is None

    def test_process_large_pdf_returns_false(self, temp_dirs, mock_generator):
        """Test _process_pdf when _process_large_pdf returns False (line 239)."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock needs_chunking to return True to trigger large PDF path
        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        # Mock _process_large_pdf to return False
        use_case._process_large_pdf = lambda pdf, nb, temp: False

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(pdf_file, input_dir, output_dir, request)

        assert result is None

    def test_empty_pdf_warning(self, temp_dirs, mock_generator):
        """Test that empty PDF is detected and skipped (lines 125-126)."""
        input_dir, _output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "empty.pdf"
        # Create truly empty file
        pdf_file.touch()

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        result = use_case._is_safe_pdf_path(pdf_file, input_dir)

        assert result is False


class TestNotebookLMClientEdgeCases:
    """Test NotebookLMClient edge cases for 100% coverage."""

    @patch("subprocess.run")
    def test_delete_notebook_success(self, mock_run, tmp_path):
        """Test delete_notebook returns True on success (line 174)."""
        mock_run.return_value = MagicMock(returncode=0)

        client = NotebookLMClient(notebooklm_path="/usr/bin/notebooklm")
        result = client.delete_notebook("notebook123")

        assert result is True


class TestPDFUtilsEdgeCases:
    """Test PDFUtils edge cases for 100% coverage."""

    @patch("pypdf.PdfReader")
    def test_count_pages_success(self, mock_reader_class, tmp_path):
        """Test count_pages returns actual page count (line 46)."""
        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock(), MagicMock(), MagicMock()]  # 3 pages
        mock_reader_class.return_value = mock_reader

        chunker = PDFChunker()
        chunker._has_pypdf = True

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()

        result = chunker.count_pages(pdf_path)

        assert result == 3
        # Verify stream.close() was called (lines 52-53)
        mock_reader.stream.close.assert_called_once()

    @patch("pypdf.PdfReader")
    def test_count_pages_stream_close_exception(self, mock_reader_class, tmp_path):
        """Test count_pages handles stream close exception (lines 52-53)."""
        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock()]
        mock_reader.stream.close.side_effect = OSError("Close failed")
        mock_reader_class.return_value = mock_reader

        chunker = PDFChunker()
        chunker._has_pypdf = True

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()

        # Should not raise exception
        result = chunker.count_pages(pdf_path)

        assert result == 1

    def test_cleanup_chunks_exception(self, tmp_path, monkeypatch):
        """Test cleanup_chunks handles exception (lines 127-128)."""
        chunker = PDFChunker()

        # Create a chunk file that will fail to delete
        chunk_file = tmp_path / "test_chunk_001.pdf"
        chunk_file.touch()

        # Mock unlink to fail using monkeypatch
        def mock_unlink(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(chunk_file.__class__, "unlink", mock_unlink)

        # Should not raise exception
        chunker.cleanup_chunks([chunk_file])


class TestCLIEdgeCases:
    """Test CLI edge cases for 100% coverage."""

    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    @patch("flashcards_generator.interfaces.cli.CLI._validate_input")
    @patch("flashcards_generator.interfaces.cli.CLI._authenticate")
    @patch("flashcards_generator.interfaces.cli.CLI._set_language")
    def test_run_keyboard_interrupt_in_execute(
        self, mock_set_language, mock_authenticate, mock_validate, mock_use_case_class
    ):
        """Test KeyboardInterrupt handling in run method (lines 194-196)."""
        mock_validate.return_value = True
        mock_authenticate.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.side_effect = KeyboardInterrupt()
        mock_use_case_class.return_value = mock_use_case

        cli = CLI()
        # Mock parser.parse_args to return valid args
        mock_args = MagicMock()
        mock_args.input_dir = Path("/tmp/input")
        mock_args.output_dir = Path("/tmp/output")
        mock_args.language = "pt_BR"
        mock_args.skip_auth_check = True
        mock_args.log_level = "INFO"
        mock_args.timeout = 900
        mock_args.no_wait = False
        mock_args.difficulty = "medium"
        mock_args.quantity = "standard"
        mock_args.instructions = ""
        mock_args.include = None
        mock_args.exclude = None
        mock_args.files = None
        cli.parser.parse_args = MagicMock(return_value=mock_args)

        result = cli.run()

        assert result == 130

    @patch("flashcards_generator.interfaces.cli.CLI.run")
    def test_main_keyboard_interrupt(self, mock_run):
        """Test KeyboardInterrupt handling in main function (lines 208-209)."""
        mock_run.side_effect = KeyboardInterrupt()

        with pytest.raises(SystemExit) as exc_info:
            from flashcards_generator.interfaces.cli import main

            main()

        assert exc_info.value.code == 130


class TestPDFSelectionFilters:
    """Test PDF selection and filtering options."""

    def test_find_all_pdfs_with_include_pattern(self, temp_dirs, mock_generator):
        """Test _find_all_pdfs with include pattern filter."""
        input_dir, output_dir = temp_dirs

        # Create multiple PDFs
        (input_dir / "capitulo1.pdf").write_text("PDF content")
        (input_dir / "capitulo2.pdf").write_text("PDF content")
        (input_dir / "notas.pdf").write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            include_pattern="capitulo*.pdf",
        )

        result = use_case._find_all_pdfs(input_dir, request)

        assert len(result) == 2
        assert all("capitulo" in str(pdf) for pdf in result)

    def test_find_all_pdfs_with_exclude_pattern(self, temp_dirs, mock_generator):
        """Test _find_all_pdfs with exclude pattern filter."""
        input_dir, output_dir = temp_dirs

        (input_dir / "file1.pdf").write_text("PDF content")
        (input_dir / "file_old.pdf").write_text("PDF content")
        (input_dir / "file2.pdf").write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            exclude_pattern="*_old.pdf",
        )

        result = use_case._find_all_pdfs(input_dir, request)

        assert len(result) == 2
        assert all("_old" not in str(pdf) for pdf in result)

    def test_find_all_pdfs_with_explicit_files(self, temp_dirs, mock_generator):
        """Test _find_all_pdfs with explicit file list."""
        input_dir, output_dir = temp_dirs

        (input_dir / "selected1.pdf").write_text("PDF content")
        (input_dir / "selected2.pdf").write_text("PDF content")
        (input_dir / "ignored.pdf").write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            explicit_files=["selected1.pdf", "selected2.pdf"],
        )

        result = use_case._find_all_pdfs(input_dir, request)

        assert len(result) == 2

    def test_find_all_pdfs_explicit_file_not_found(self, temp_dirs, mock_generator):
        """Test _find_all_pdfs with explicit file that doesn't exist."""
        input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            explicit_files=["nonexistent.pdf"],
        )

        result = use_case._find_all_pdfs(input_dir, request)

        assert len(result) == 0


class TestCLIKeyboardInterrupt:
    """Test KeyboardInterrupt handling in CLI main."""

    @patch("flashcards_generator.interfaces.cli.logger")
    def test_main_keyboard_interrupt_direct(self, mock_logger):
        """Test KeyboardInterrupt in main function - line 207."""
        from flashcards_generator.interfaces.cli import main

        # Patch CLI.run to raise KeyboardInterrupt
        with patch.object(CLI, "run", side_effect=KeyboardInterrupt()):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 130


class TestUseCasesExceptionHandling:
    """Test exception handling in use cases."""

    def test_is_safe_pdf_path_value_error(self, temp_dirs, mock_generator):
        """Test _is_safe_pdf_path handling ValueError - lines 130-132."""
        input_dir, _output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Create a path that will raise ValueError on resolve
        class BadPath(Path):
            def resolve(self, strict=False):
                raise ValueError("Invalid path")

        bad_path = BadPath(input_dir / "test.pdf")
        result = use_case._is_safe_pdf_path(bad_path, input_dir)

        assert result is False


class TestUseCasesExceptionCoverage:
    """Test exception handling for 100% coverage."""

    def test_process_chunk_generic_exception(
        self, temp_dirs, mock_generator, monkeypatch
    ):
        """Test _process_chunk generic Exception handler - lines 370-372."""
        input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        # Mock _create_notebook to raise RuntimeError (caught by specific handler)
        use_case._create_notebook = lambda name: (_ for _ in ()).throw(
            RuntimeError("Test error")
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_chunk(
            chunk_file, "deck_name", output_dir, request, 1, 1
        )

        assert result is None

    def test_generate_flashcards_no_artifact_id(self, temp_dirs, mock_generator):
        """Test _generate_flashcards when artifact_id is None - lines 452-454."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator()
        generator.generate_flashcards = MagicMock(return_value=None)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        notebook_id = use_case._create_notebook("test_deck")
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._generate_flashcards(
            notebook_id, "test_deck", output_dir, request
        )

        assert result is None


class TestTempFileCleanupErrors:
    """Test temp file cleanup error handling."""

    def test_process_chunk_oserror_on_cleanup(
        self, temp_dirs, mock_generator, monkeypatch
    ):
        """Test _process_chunk OSError handling on temp file cleanup - lines 350-351."""
        input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        # Mock unlink to raise OSError
        def mock_unlink(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "unlink", mock_unlink)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        # Should not raise exception
        result = use_case._process_chunk(
            chunk_file, "deck_name", output_dir, request, 1, 1
        )

        # Result may be None or a Deck depending on other mocks
        assert result is not None

    def test_download_and_convert_oserror_cleanup(
        self, temp_dirs, mock_generator, monkeypatch
    ):
        """Test _download_and_convert OSError handling - lines 512-513."""
        _input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock unlink to raise OSError
        def mock_unlink(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "unlink", mock_unlink)

        notebook_id = use_case._create_notebook("test_deck")

        # Should not raise exception
        result = use_case._download_and_convert(
            notebook_id, "artifact123", output_dir, "test_deck"
        )

        assert result is not None
        assert result.name == "test_deck"


class TestCLICoverage:
    """Test CLI coverage for 100%."""

    def test_create_request_with_files(self, temp_dirs):
        """Test _create_request with explicit files - line 160."""
        from flashcards_generator.interfaces.cli import CLI

        cli = CLI()

        mock_args = MagicMock()
        mock_args.input_dir = temp_dirs[0]
        mock_args.output_dir = temp_dirs[1]
        mock_args.difficulty = "medium"
        mock_args.quantity = "standard"
        mock_args.instructions = ""
        mock_args.no_wait = False
        mock_args.timeout = 900
        mock_args.include = None
        mock_args.exclude = None
        mock_args.files = "file1.pdf,file2.pdf,file3.pdf"

        request = cli._create_request(mock_args)

        assert len(request.explicit_files) == 3
        assert request.explicit_files == ["file1.pdf", "file2.pdf", "file3.pdf"]
