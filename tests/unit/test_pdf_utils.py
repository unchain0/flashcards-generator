"""Tests for PDF utilities."""

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.errors import EmptyFileError

from flashcards_generator.integrations.pdf_utils import (
    PDFChunker,
    _ChapterAccumulator,
)


class TestPDFChunker:
    """Test PDFChunker functionality."""

    def test_init_default(self):
        chunker = PDFChunker()
        assert chunker.chunk_size == 30
        assert chunker.overlap_pages == 5
        assert chunker.DEFAULT_THRESHOLD == 50

    def test_init_custom_params(self):
        chunker = PDFChunker(chunk_size=30, overlap_pages=3)
        assert chunker.chunk_size == 30
        assert chunker.overlap_pages == 3

    @patch(
        "flashcards_generator.integrations.pdf_utils.PDFChunker._check_pypdf"
    )
    def test_check_pypdf_available(self, mock_check):
        mock_check.return_value = True
        chunker = PDFChunker()
        assert chunker._has_pypdf is True

    @patch(
        "flashcards_generator.integrations.pdf_utils.PDFChunker._check_pypdf"
    )
    def test_check_pypdf_unavailable(self, mock_check):
        mock_check.return_value = False
        chunker = PDFChunker()
        assert chunker._has_pypdf is False

    def test_count_pages_no_pypdf(self, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = False
        pdf_path = tmp_path / "test.pdf"
        assert chunker.count_pages(pdf_path) == 0

    def test_count_pages_rejects_oversized_pdf(self, tmp_path, monkeypatch):
        """PDF parsing must enforce a finite input-size limit."""
        pdf_path = tmp_path / "oversized.pdf"
        pdf_path.write_bytes(b"012345")
        monkeypatch.setattr(PDFChunker, "MAX_PDF_FILE_BYTES", 5, raising=False)
        chunker = PDFChunker()

        with pytest.raises(ValueError, match="maximum"):
            chunker.count_pages(pdf_path)

    @patch(
        "flashcards_generator.integrations.pdf_utils.PDFChunker.count_pages"
    )
    def test_needs_chunking_no_pypdf(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = False
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is False
        mock_count.assert_not_called()

    @patch(
        "flashcards_generator.integrations.pdf_utils.PDFChunker.count_pages"
    )
    def test_needs_chunking_below_threshold(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True
        mock_count.return_value = 25
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is False

    @patch(
        "flashcards_generator.integrations.pdf_utils.PDFChunker.count_pages"
    )
    def test_needs_chunking_above_threshold(self, mock_count, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True
        mock_count.return_value = 250
        pdf_path = tmp_path / "test.pdf"
        assert chunker.needs_chunking(pdf_path) is True

    @patch(
        "flashcards_generator.integrations.pdf_utils.PDFChunker.count_pages"
    )
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
    @pytest.mark.parametrize("use_chapters", [True, False])
    def test_chunk_pdf_success(
        self,
        mock_writer_class,
        mock_reader_class,
        use_chapters,
        tmp_path,
    ):
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

        chunks = list(
            chunker.chunk_pdf(pdf_path, output_dir, use_chapters=use_chapters)
        )

        assert len(chunks) == 3
        assert mock_writer.add_page.call_count == 5

    def test_duplicate_chapter_title_is_recorded_once(self) -> None:
        source = PdfWriter()
        source.add_blank_page(width=72, height=72)
        source.add_blank_page(width=72, height=72)
        stream = BytesIO()
        source.write(stream)
        stream.seek(0)
        reader = PdfReader(stream)
        accumulator = _ChapterAccumulator(PdfWriter())

        PDFChunker._append_chapter(reader, accumulator, 0, 1, "Repeated", True)
        PDFChunker._append_chapter(reader, accumulator, 1, 2, "Repeated", True)

        assert accumulator.titles == ["Repeated"]
        assert accumulator.relevant_titles == ["Repeated"]
        assert accumulator.end == 2
        assert accumulator.pages == 2
        assert len(accumulator.writer.pages) == 2

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

    def test_count_pages_returns_page_count_and_closes_reader(
        self, tmp_path: Path
    ) -> None:
        chunker = PDFChunker()
        chunker._has_pypdf = True
        pdf_path = tmp_path / "valid.pdf"
        pdf_path.touch()
        reader = Mock(pages=[Mock(), Mock()])

        with patch("pypdf.PdfReader", return_value=reader):
            assert chunker.count_pages(pdf_path) == 2

        reader.stream.close.assert_called_once_with()

    def test_cleanup_chunks_exception(self, tmp_path):
        chunker = PDFChunker()

        mock_path = Mock(spec=Path)
        mock_path.name = "test_chunk_001.pdf"
        mock_path.unlink.side_effect = PermissionError("Access denied")

        chunker.cleanup_chunks([mock_path])

        mock_path.unlink.assert_called_once_with(missing_ok=True)

    def test_check_pypdf_import_error(self):
        with patch(
            "builtins.__import__",
            side_effect=ImportError("No module named pypdf"),
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
    def test_get_chapter_boundaries_no_outline(
        self, mock_reader_class, tmp_path
    ):
        chunker = PDFChunker()
        chunker._has_pypdf = True

        mock_reader = Mock()
        mock_reader.outline = None
        mock_reader.pages = [Mock(), Mock()]
        mock_reader_class.return_value = mock_reader

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()

        assert chunker.get_chapter_boundaries(pdf_path) == []
        mock_reader_class.assert_called_once_with(str(pdf_path), strict=False)

    def test_rejects_invalid_chunk_configuration(self):
        with pytest.raises(ValueError, match="chunk_size"):
            PDFChunker(chunk_size=0)

        with pytest.raises(ValueError, match="overlap_pages"):
            PDFChunker(chunk_size=30, overlap_pages=30)

        with pytest.raises(ValueError, match="overlap_pages"):
            PDFChunker(chunk_size=30, overlap_pages=-1)

    def test_fixed_size_ranges_use_single_overlap_and_close_reader(
        self, tmp_path
    ):
        chunker = PDFChunker(chunk_size=30, overlap_pages=5)
        chunker._has_pypdf = True
        reader = Mock()
        reader.pages = list(range(51))
        chunker._create_reader = Mock(return_value=reader)
        writers = []

        def create_writer():
            writer = Mock()
            writers.append(writer)
            return writer

        with patch("pypdf.PdfWriter", side_effect=create_writer):
            chunks = list(
                chunker._chunk_fixed_size_with_overlap(
                    tmp_path / "source.pdf", tmp_path / "output"
                )
            )

        assert [
            [call.args[0] for call in writer.add_page.call_args_list]
            for writer in writers
        ] == [list(range(30)), list(range(25, 51))]
        assert len(chunks) == 2
        reader.stream.close.assert_called_once_with()

    def test_fixed_size_reader_closes_when_generator_is_closed(self, tmp_path):
        chunker = PDFChunker(chunk_size=2, overlap_pages=0)
        reader = Mock()
        reader.pages = list(range(3))
        chunker._create_reader = Mock(return_value=reader)

        with patch("pypdf.PdfWriter", return_value=Mock()):
            chunks = chunker._chunk_fixed_size_with_overlap(
                tmp_path / "source.pdf", tmp_path / "output"
            )
            next(chunks)
            chunks.close()

        reader.stream.close.assert_called_once_with()

    def test_chapter_chunk_preserves_leading_pages(self, tmp_path):
        chunker = PDFChunker(chunk_size=30, overlap_pages=0)
        reader = Mock()
        reader.pages = list(range(10))
        chunker._create_reader = Mock(return_value=reader)
        writer = Mock()

        with patch("pypdf.PdfWriter", return_value=writer):
            chunks = list(
                chunker._chunk_by_chapters(
                    tmp_path / "source.pdf",
                    tmp_path / "output",
                    [(5, 10, "Chapter")],
                )
            )

        assert [
            call.args[0] for call in writer.add_page.call_args_list
        ] == list(range(10))
        assert len(chunks) == 1
        reader.stream.close.assert_called_once_with()

    def test_corrupt_pdf_returns_controlled_fallbacks(self, tmp_path):
        chunker = PDFChunker()
        chunker._has_pypdf = True
        chunker._create_reader = Mock(side_effect=EmptyFileError("empty"))
        pdf_path = tmp_path / "corrupt.pdf"

        assert chunker.count_pages(pdf_path) == 0
        assert chunker.get_chapter_boundaries(pdf_path) == []
        assert list(chunker.chunk_pdf(pdf_path, tmp_path / "output")) == []

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
        mock_reader.get_page_number.side_effect = lambda p: (
            0 if p == mock_page1 else 1
        )
        mock_reader_class.return_value = mock_reader

        pdf_path = tmp_path / "test.pdf"
        pdf_path.touch()

        chapters = chunker.get_chapter_boundaries(pdf_path)
        assert len(chapters) == 2
        assert chapters[0] == (0, 1, "Chapter 1")
        assert chapters[1] == (1, 3, "Chapter 2")
        mock_reader_class.assert_called_once_with(str(pdf_path), strict=False)

    def test_count_pages_rejects_page_count_above_limit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_path = tmp_path / "too-many-pages.pdf"
        pdf_path.touch()
        monkeypatch.setattr(PDFChunker, "MAX_PDF_PAGES", 1)
        chunker = PDFChunker()
        reader = Mock(pages=[Mock(), Mock()])
        chunker._create_reader = Mock(return_value=reader)

        with pytest.raises(ValueError, match="maximum page count"):
            chunker.count_pages(pdf_path)

        reader.stream.close.assert_called_once_with()

    @patch("pypdf.PdfReader")
    def test_chapter_boundaries_skip_invalid_items_and_end_at_page_limit(
        self, mock_reader_class: Mock, tmp_path: Path
    ) -> None:
        chunker = PDFChunker()
        chunker._has_pypdf = True
        valid_page = Mock()
        reader = Mock()
        reader.pages = [Mock(), Mock()]
        reader.outline = [
            "section label",
            {"/Title": "missing page"},
            {"/Title": "unmapped", "/Page": Mock()},
            {"/Title": "Chapter", "/Page": valid_page},
            "section end",
        ]
        reader.get_page_number.side_effect = lambda page: (
            0 if page is valid_page else None
        )
        mock_reader_class.return_value = reader
        pdf_path = tmp_path / "outlined.pdf"
        pdf_path.touch()

        assert chunker.get_chapter_boundaries(pdf_path) == [(0, 2, "Chapter")]

        reader.stream.close.assert_called_once_with()

    def test_chunk_pdf_uses_chapters_and_filters_irrelevant_chunks(
        self, tmp_path: Path
    ) -> None:
        chunker = PDFChunker(chunk_size=2, overlap_pages=0)
        chunker._has_pypdf = True
        chapters = [
            (0, 2, "Chapter 1"),
            (2, 4, "Chapter 2"),
            (4, 6, "Index"),
        ]
        chunker.get_chapter_boundaries = Mock(return_value=chapters)
        reader = Mock(pages=list(range(6)))
        chunker._create_reader = Mock(return_value=reader)
        writers: list[Mock] = []

        def create_writer() -> Mock:
            writer = Mock()
            writers.append(writer)
            return writer

        source = tmp_path / "source.pdf"
        source.touch()
        output_dir = tmp_path / "chunks"
        with patch("pypdf.PdfWriter", side_effect=create_writer):
            chunks = list(chunker.chunk_pdf(source, output_dir))

        assert [chunk.name for chunk in chunks] == [
            "source_chunk_001.pdf",
            "source_chunk_002.pdf",
        ]
        assert [
            [call.args[0] for call in writer.add_page.call_args_list]
            for writer in writers
        ] == [[0, 1], [2, 3], [4, 5]]
        assert [writer.write.call_count for writer in writers] == [1, 1, 0]
        reader.stream.close.assert_called_once_with()

    def test_chapter_chunks_reuse_only_the_configured_relevant_overlap(
        self, tmp_path: Path
    ) -> None:
        chunker = PDFChunker(chunk_size=2, overlap_pages=1)
        reader = Mock(pages=list(range(4)))
        chunker._create_reader = Mock(return_value=reader)
        writers: list[Mock] = []

        def create_writer() -> Mock:
            writer = Mock()
            writers.append(writer)
            return writer

        with patch("pypdf.PdfWriter", side_effect=create_writer):
            chunks = list(
                chunker._chunk_by_chapters(
                    tmp_path / "source.pdf",
                    tmp_path / "chunks",
                    [(0, 2, "Chapter 1"), (2, 4, "Chapter 2")],
                    use_overlap=True,
                )
            )

        assert len(chunks) == 2
        assert [
            [call.args[0] for call in writer.add_page.call_args_list]
            for writer in writers
        ] == [[0, 1], [1, 2, 3]]

    def test_empty_chapter_list_produces_no_empty_chunk(
        self, tmp_path: Path
    ) -> None:
        chunker = PDFChunker()
        reader = Mock(pages=[Mock()])
        chunker._create_reader = Mock(return_value=reader)

        assert (
            list(
                chunker._chunk_by_chapters(
                    tmp_path / "source.pdf", tmp_path / "chunks", []
                )
            )
            == []
        )

        reader.stream.close.assert_called_once_with()
