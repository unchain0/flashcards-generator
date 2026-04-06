"""Tests for PDF utilities."""

from pathlib import Path
from unittest.mock import Mock, patch

from flashcards_generator.infrastructure.pdf_utils import PDFChunker


class TestPDFChunker:
    """Test PDFChunker functionality."""

    def test_init_default(self):
        chunker = PDFChunker()
        assert chunker.chunk_size == 30
        assert chunker.overlap_pages == 5
        assert chunker.DEFAULT_THRESHOLD == 30

    def test_init_custom_params(self):
        chunker = PDFChunker(chunk_size=30, overlap_pages=3)
        assert chunker.chunk_size == 30
        assert chunker.overlap_pages == 3

    @patch("flashcards_generator.infrastructure.pdf_utils.PDFChunker._check_pypdf")
    def test_check_pypdf_available(self, mock_check):
        mock_check.return_value = True
        chunker = PDFChunker()
        assert chunker._has_pypdf is True

    @patch("flashcards_generator.infrastructure.pdf_utils.PDFChunker._check_pypdf")
    def test_check_pypdf_unavailable(self, mock_check):
        mock_check.return_value = False
        chunker = PDFChunker()
        assert chunker._has_pypdf is False

    def test_count_pages_no_pypdf(self, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = False
        pdf_path = tmp_path / "test.pdf"
        assert chunker.count_pages(pdf_path) == 0

    @patch("flashcards_generator.infrastructure.pdf_utils.PDFChunker.count_pages")
    def test_needs_chunking_no_pypdf(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = False
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is False
        mock_count.assert_not_called()

    @patch("flashcards_generator.infrastructure.pdf_utils.PDFChunker.count_pages")
    def test_needs_chunking_below_threshold(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True
        mock_count.return_value = 25
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is False

    @patch("flashcards_generator.infrastructure.pdf_utils.PDFChunker.count_pages")
    def test_needs_chunking_above_threshold(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True
        mock_count.return_value = 250
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is True

    @patch("flashcards_generator.infrastructure.pdf_utils.PDFChunker.count_pages")
    def test_needs_chunking_zero_pages(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True
        mock_count.return_value = 0
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is False

    def test_chunk_pdf_no_pypdf(self, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = False
        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()
        chunks = list(chunker.chunk_pdf(pdf_path, tmp_path / "output"))
        assert len(chunks) == 1
        assert chunks[0] == pdf_path

    @patch("pypdf.PdfReader")
    @patch("pypdf.PdfWriter")
    def test_chunk_pdf_success(self, mock_writer_class, mock_reader_class, tmp_path):
        chunker = PDFChunker(chunk_size=2, overlap_pages=0)
        chunker._has_pypdf = True

        mock_reader = Mock()
        mock_reader.pages = [Mock(), Mock(), Mock(), Mock(), Mock()]
        mock_reader.outline = None
        mock_reader_class.return_value = mock_reader

        mock_writer = Mock()
        mock_writer_class.return_value = mock_writer

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()
        output_dir = tmp_path / "output"

        chunks = list(chunker.chunk_pdf(pdf_path, output_dir))

        assert len(chunks) == 3
        assert mock_writer.add_page.call_count == 5

    def test_cleanup_chunks(self, tmp_path):
        chunker = PDFChunker()

        # Create test chunk files
        chunk1 = tmp_path / "test_chunk_001.pdf"
        chunk2 = tmp_path / "test_chunk_002.pdf"
        not_chunk = tmp_path / "other.pdf"

        chunk1.touch()
        chunk2.touch()
        not_chunk.touch()

        chunks = [chunk1, chunk2, not_chunk]

        chunker.cleanup_chunks(chunks)

        assert not chunk1.exists()
        assert not chunk2.exists()
        assert not_chunk.exists()

    def test_cleanup_chunks_nonexistent(self, tmp_path):
        chunker = PDFChunker()
        nonexistent = tmp_path / "nonexistent_chunk_001.pdf"

        # Should not raise error
        chunker.cleanup_chunks([nonexistent])

    def test_count_pages_error(self, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True

        with patch("pypdf.PdfReader") as mock_reader:
            mock_reader.side_effect = OSError("Read error")
            pdf_path = tmp_path / "test.pdf"
            pdf_path.touch()

            assert chunker.count_pages(pdf_path) == 0

    def test_cleanup_chunks_exception(self, tmp_path):
        chunker = PDFChunker()

        # Create a mock path that raises exception on exists()
        mock_path = Mock(spec=Path)
        mock_path.name = "test_chunk_001.pdf"
        mock_path.exists.side_effect = PermissionError("Access denied")

        # Should not raise error
        chunker.cleanup_chunks([mock_path])

    def test_check_pypdf_import_error(self):
        with patch(
            "builtins.__import__", side_effect=ImportError("No module named pypdf")
        ):
            chunker = PDFChunker()
            assert chunker._has_pypdf is False

    def test_flatten_outline(self):
        chunker = PDFChunker()
        nested = [
            "item1",
            ["item2", ["item3", "item4"]],
            "item5",
        ]
        flat = chunker._flatten_outline(nested)
        assert flat == ["item1", "item2", "item3", "item4", "item5"]

    def test_get_chapter_boundaries_no_pypdf(self, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = False
        pdf_path = tmp_path / "test.pdf"
        assert chunker.get_chapter_boundaries(pdf_path) == []

    @patch("pypdf.PdfReader")
    def test_get_chapter_boundaries_no_outline(self, mock_reader_class, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True

        mock_reader = Mock()
        mock_reader.outline = None
        mock_reader.pages = [Mock(), Mock()]
        mock_reader_class.return_value = mock_reader

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()

        assert chunker.get_chapter_boundaries(pdf_path) == []

    @patch("pypdf.PdfReader")
    def test_get_chapter_boundaries_success(self, mock_reader_class, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True

        mock_page1 = Mock()
        mock_page2 = Mock()
        mock_reader = Mock()
        mock_reader.pages = [mock_page1, mock_page2, mock_page1]
        mock_reader.outline = [
            {"/Title": "Chapter 1", "/Page": mock_page1},
            {"/Title": "Chapter 2", "/Page": mock_page2},
        ]
        mock_reader.get_page_number.side_effect = lambda p: 0 if p == mock_page1 else 1
        mock_reader_class.return_value = mock_reader

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()

        chapters = chunker.get_chapter_boundaries(pdf_path)
        assert len(chapters) == 2
        assert chapters[0] == (0, 1, "Chapter 1")
        assert chapters[1] == (1, 3, "Chapter 2")
