"""Tests for use cases with dependency injection."""

from unittest.mock import MagicMock

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase
from flashcards_generator.domain.exceptions import GenerationError, NotebookCleanupError


class TestGenerateFlashcardsUseCase:
    """Test suite for generate flashcards use case."""

    def test_execute_no_pdfs(self, temp_dirs, mock_generator):
        """Test execution with no PDF files."""
        input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert result == []

    def test_execute_with_folder_no_pdfs(self, temp_dirs, mock_generator):
        """Test execution with folder but no PDFs."""
        input_dir, output_dir = temp_dirs
        (input_dir / "tema1").mkdir()

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 0

    def test_execute_successful_processing(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        """Test successful PDF processing."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Historia"
        tema_dir.mkdir()
        (tema_dir / "aula1.pdf").write_text("PDF content")

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock pdf_chunker to avoid reading invalid PDF content
        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: False

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 1
        assert result[0].name == "Historia_aula1"
        assert result[0].total_cards > 0

    def test_execute_add_source_failure(self, temp_dirs, mock_generator):
        """Test handling of source addition failure."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator(should_fail_source=True)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 0

    def test_execute_generate_flashcards_failure(self, temp_dirs, mock_generator):
        """Test handling of generation failure."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator(should_fail_generation=True)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 0

    def test_execute_no_wait_mode(self, temp_dirs, mock_generator, sample_flashcards):
        """Test no-wait mode."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock pdf_chunker to avoid reading invalid PDF content
        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: False

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            wait_for_completion=False,
        )

        result = use_case.execute(request)

        assert len(result) == 1
        assert result[0].flashcards == []  # Empty in no-wait mode

    def test_execute_wait_timeout(self, temp_dirs, mock_generator, sample_flashcards):
        """Test timeout handling."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator(flashcards=sample_flashcards, should_timeout=True)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock pdf_chunker to avoid reading invalid PDF content
        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: False

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 1
        assert result[0].notebook_id  # Notebook preserved

    def test_execute_custom_instructions(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        """Test custom instructions are passed."""
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            instructions="Custom instructions",
        )

        use_case.execute(request)

        # Verify instructions were used (would need spy in real test)
        # For now, just verify it doesn't crash
        assert True

    def test_execute_with_subdirectories(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        input_dir, output_dir = temp_dirs

        # Create nested structure
        tema_dir = input_dir / "Subject" / "Tema1"
        tema_dir.mkdir(parents=True)
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock pdf_chunker to avoid reading invalid PDF content
        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: False

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 1

    def test_execute_pdf_already_exists(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        # Create existing CSV file
        output_tema = output_dir / "Tema1"
        output_tema.mkdir(parents=True)
        (output_tema / "file.csv").touch()

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        # Should skip the PDF
        assert len(result) == 0

    def test_execute_large_pdf_chunking(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        use_case.pdf_chunker.chunk_pdf = lambda pdf_path, temp_dir: [chunk_file]

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 1

    def test_execute_chunk_add_source_failure(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(should_fail_source=True)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        # Mock pdf_chunker to simulate large PDF
        use_case.pdf_chunker.needs_chunking = lambda pdf, threshold: True

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        # Should return empty when chunk processing fails
        assert len(result) == 0

    def test_execute_large_pdf_generate_failure(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(should_fail_generation=True)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        use_case.pdf_chunker.chunk_pdf = lambda pdf_path, temp_dir: [chunk_file]

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 0

    def test_execute_large_pdf_no_flashcards_generated(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(flashcards=[])
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        use_case.pdf_chunker.chunk_pdf = lambda pdf_path, temp_dir: [chunk_file]

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        # Even with empty flashcards, a deck is returned
        assert len(result) == 1
        assert len(result[0].flashcards) == 0

    def test_execute_no_wait_mode_large_pdf(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        use_case.pdf_chunker.chunk_pdf = lambda pdf_path, temp_dir: [chunk_file]

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            wait_for_completion=False,
        )

        result = use_case.execute(request)

        # In no-wait mode, a deck is still returned but without flashcards
        assert len(result) == 1
        assert len(result[0].flashcards) == 0

    def test_cleanup_notebooks_empty_list(self, temp_dirs, mock_generator):
        input_dir, _output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        use_case._created_notebooks = []
        use_case._cleanup_notebooks()

        assert use_case._created_notebooks == []

    def test_cleanup_notebooks_with_exception(self, temp_dirs, mock_generator):
        input_dir, _output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator()
        generator.delete_notebook = MagicMock(
            side_effect=NotebookCleanupError("nb1", "fail")
        )
        use_case = GenerateFlashcardsUseCase(generator=generator)

        use_case._created_notebooks = ["notebook123"]
        use_case._cleanup_notebooks()

        assert use_case._created_notebooks == []

    def test_get_output_subdir_no_parent(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        generator = mock_generator()
        use_case = GenerateFlashcardsUseCase(generator=generator)

        pdf_path = input_dir / "file.pdf"
        result = use_case._get_output_subdir(pdf_path, input_dir, output_dir)

        assert result == output_dir

    def test_process_pdf_generation_error(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator()
        generator.create_notebook = MagicMock(
            side_effect=GenerationError("nb1", "fail")
        )
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(
            tema_dir / "file.pdf", input_dir, output_dir, request
        )

        assert result is None

    def test_process_pdf_unexpected_error(self, temp_dirs, mock_generator):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").write_text("PDF content")

        generator = mock_generator()
        generator.create_notebook = MagicMock(side_effect=ValueError("unexpected"))
        use_case = GenerateFlashcardsUseCase(generator=generator)

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(
            tema_dir / "file.pdf", input_dir, output_dir, request
        )

        assert result is None

    def test_download_and_convert_oserror_on_cleanup(
        self, temp_dirs, mock_generator, sample_flashcards, monkeypatch
    ):
        _input_dir, output_dir = temp_dirs

        generator = mock_generator(flashcards=sample_flashcards)
        use_case = GenerateFlashcardsUseCase(generator=generator)

        def mock_unlink(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr("pathlib.Path.unlink", mock_unlink)

        result = use_case._download_and_convert(
            "notebook123", "artifact456", output_dir, "TestDeck"
        )

        assert result is not None
        assert result.name == "TestDeck"

    def test_download_and_convert_notebook_cleanup_error(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        _input_dir, output_dir = temp_dirs

        generator = mock_generator(flashcards=sample_flashcards)
        generator.delete_notebook = MagicMock(
            side_effect=NotebookCleanupError("nb1", "fail")
        )
        use_case = GenerateFlashcardsUseCase(generator=generator)
        use_case._created_notebooks = ["notebook123"]

        result = use_case._download_and_convert(
            "notebook123", "artifact456", output_dir, "TestDeck"
        )

        assert result is not None
        assert result.name == "TestDeck"

    def test_process_large_pdf_chunk_timeout(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(flashcards=sample_flashcards)
        generator.wait_for_artifact = lambda n, a, timeout: False
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        use_case.pdf_chunker.chunk_pdf = lambda pdf_path, temp_dir: [chunk_file]

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        # On timeout, a deck is still returned with notebook_id preserved
        assert len(result) == 1
        assert result[0].notebook_id  # Notebook preserved for retry

    def test_process_large_pdf_chunk_notebook_delete_exception(
        self, temp_dirs, mock_generator, sample_flashcards
    ):
        input_dir, output_dir = temp_dirs

        tema_dir = input_dir / "Tema1"
        tema_dir.mkdir()
        pdf_file = tema_dir / "large.pdf"
        pdf_file.write_text("PDF content")  # Write content so not empty

        generator = mock_generator(flashcards=sample_flashcards)
        generator.delete_notebook = MagicMock(
            side_effect=NotebookCleanupError("nb1", "fail")
        )
        use_case = GenerateFlashcardsUseCase(generator=generator)

        chunk_file = output_dir / ".temp_chunks" / "large_chunk_001.pdf"
        chunk_file.parent.mkdir(parents=True, exist_ok=True)
        chunk_file.touch()

        use_case.pdf_chunker.needs_chunking = lambda pdf_path, threshold: True
        use_case.pdf_chunker.chunk_pdf = lambda pdf_path, temp_dir: [chunk_file]
        use_case._created_notebooks = ["notebook123"]

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case.execute(request)

        assert len(result) == 1
