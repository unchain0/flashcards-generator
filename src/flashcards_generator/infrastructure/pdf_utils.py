"""PDF manipulation utilities for chunking large files."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from flashcards_generator.infrastructure.document_limits import (
    MAX_PDF_FILE_BYTES as DEFAULT_MAX_PDF_FILE_BYTES,
)
from flashcards_generator.infrastructure.document_limits import (
    MAX_PDF_PAGES as DEFAULT_MAX_PDF_PAGES,
)
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Generator

    from pypdf import PdfReader, PdfWriter

logger = get_logger("pdf_utils")


@dataclass(slots=True)
class _ChapterAccumulator:
    """Mutable PDF writer state while accumulating chapter pages."""

    writer: PdfWriter
    start: int = 0
    end: int = 0
    pages: int = 0
    titles: list[str] = field(default_factory=list)
    relevant_titles: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ChapterChunk:
    """A finalized chapter chunk ready to be written to disk."""

    writer: PdfWriter
    titles: list[str]
    start: int
    end: int
    relevant_titles: list[str]


class PPTXConverter:
    """Converts PowerPoint (.pptx) files to PDF format."""

    PROCESS_CLEANUP_TIMEOUT = 5

    def __init__(self) -> None:
        self._has_libreoffice = self._check_libreoffice()

    def _check_libreoffice(self) -> bool:
        """Check if LibreOffice is available."""
        try:
            result = subprocess.run(
                ["soffice", "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("LibreOffice not found. PPTX conversion disabled.")
            return False

    def convert(self, pptx_path: Path, output_dir: Path) -> Path | None:
        """Convert PPTX to PDF using LibreOffice."""
        if not self._has_libreoffice:
            logger.error(
                f"Cannot convert {pptx_path.name}: LibreOffice not available"
            )
            return None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)

            pdf_name = pptx_path.stem + ".pdf"
            pdf_path = output_dir / pdf_name
            with tempfile.TemporaryDirectory(
                prefix=f".{pptx_path.stem}-", dir=output_dir
            ) as conversion_dir:
                converted_pdf_path = Path(conversion_dir) / pdf_name
                result = self._run_conversion([
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    conversion_dir,
                    str(pptx_path),
                ])

                if result.returncode != 0:
                    logger.error(
                        f"PPTX conversion failed: {result.stderr[:500]}"
                    )
                    return None

                if not converted_pdf_path.is_file():
                    logger.error(
                        f"PDF not found after conversion: {converted_pdf_path}"
                    )
                    return None

                converted_pdf_path.replace(pdf_path)

            logger.info(f"Converted {pptx_path.name} → {pdf_name}")
            return pdf_path

        except subprocess.TimeoutExpired:
            logger.error(f"PPTX conversion timeout: {pptx_path.name}")
            return None
        except OSError as e:
            logger.error(f"PPTX conversion error: {e}")
            return None

    def _run_conversion(
        self, command: list[str]
    ) -> subprocess.CompletedProcess[str]:
        """Run LibreOffice in an isolated process group."""
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=False,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=120)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            self._stop_process(process)
            raise

        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        """Stop LibreOffice and reap its process group."""
        self._signal_process(process, signal.SIGTERM)
        try:
            process.communicate(timeout=self.PROCESS_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._signal_process(process, signal.SIGKILL)
            process.communicate(timeout=self.PROCESS_CLEANUP_TIMEOUT)

    @staticmethod
    def _signal_process(
        process: subprocess.Popen[str], signal_number: int
    ) -> None:
        """Signal the isolated group, falling back to its leader."""
        pid = getattr(process, "pid", None)
        if os.name == "posix" and isinstance(pid, int):
            try:
                os.killpg(pid, signal_number)
                return
            except (OSError, ProcessLookupError):
                pass
        if signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()


class PDFChunker:
    """Handles PDF page counting and chunking for large files."""

    MAX_PDF_FILE_BYTES: ClassVar[int] = DEFAULT_MAX_PDF_FILE_BYTES
    MAX_PDF_PAGES: ClassVar[int] = DEFAULT_MAX_PDF_PAGES
    DEFAULT_CHUNK_SIZE = (
        30  # Optimal for flashcard quality (NVIDIA benchmark: 20-30 pages)
    )
    DEFAULT_THRESHOLD = 50
    DEFAULT_OVERLAP_PAGES = 5  # 10% overlap for context continuity

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap_pages: int = DEFAULT_OVERLAP_PAGES,
    ):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if not 0 <= overlap_pages < chunk_size:
            raise ValueError(
                "overlap_pages must be non-negative and less than chunk_size"
            )

        self.chunk_size = chunk_size
        self.overlap_pages = overlap_pages
        self._has_pypdf = self._check_pypdf()

    def _check_pypdf(self) -> bool:
        try:
            import pypdf as _pypdf

            return bool(_pypdf)
        except ImportError:
            logger.warning("pypdf not installed. PDF chunking disabled.")
            return False

    def _create_reader(self, pdf_path: Path) -> PdfReader:
        """Create a forgiving PDF reader for real-world malformed files."""
        from pypdf import PdfReader

        return PdfReader(str(pdf_path), strict=False)

    def _validate_pdf_file(self, pdf_path: Path) -> None:
        """Reject oversized PDF inputs before handing them to pypdf."""
        file_size = pdf_path.stat().st_size
        if file_size > self.MAX_PDF_FILE_BYTES:
            raise ValueError(
                f"PDF exceeds maximum size of "
                f"{self.MAX_PDF_FILE_BYTES} bytes: {pdf_path}"
            )

    def _validate_page_count(self, pdf_path: Path, page_count: int) -> None:
        """Reject PDFs whose page count could exhaust chunking resources."""
        if page_count > self.MAX_PDF_PAGES:
            raise ValueError(
                f"PDF exceeds maximum page count of "
                f"{self.MAX_PDF_PAGES}: {pdf_path}"
            )

    def count_pages(self, pdf_path: Path) -> int:
        """Count pages in PDF file."""
        if not self._has_pypdf:
            return 0

        from pypdf.errors import PdfReadError

        reader = None
        try:
            self._validate_pdf_file(pdf_path)
            reader = self._create_reader(pdf_path)
            page_count = len(reader.pages)
            self._validate_page_count(pdf_path, page_count)
            return page_count
        except (OSError, ImportError, PdfReadError, RuntimeError) as e:
            logger.error(f"Failed to count pages in {pdf_path}: {e}")
            return 0
        finally:
            if reader is not None:
                with contextlib.suppress(OSError):
                    reader.stream.close()

    def get_chapter_boundaries(
        self, pdf_path: Path
    ) -> list[tuple[int, int, str]]:
        """Extract chapter boundaries from PDF outline/bookmarks."""
        if not self._has_pypdf:
            return []
        from pypdf.errors import PdfReadError

        reader = None
        try:
            self._validate_pdf_file(pdf_path)
            reader = self._create_reader(pdf_path)
            total_pages = len(reader.pages)
            self._validate_page_count(pdf_path, total_pages)
            if not reader.outline:
                return []
            items = self._flatten_outline(reader.outline)
            chapters = self._collect_chapter_boundaries(
                reader, items, total_pages
            )
            logger.info(f"Found {len(chapters)} chapters in {pdf_path.name}")
            return chapters
        except (OSError, ImportError, PdfReadError, RuntimeError) as e:
            logger.warning(
                f"Failed to extract chapter boundaries from {pdf_path}: {e}"
            )
            return []
        finally:
            if reader is not None:
                with contextlib.suppress(OSError):
                    reader.stream.close()

    def _collect_chapter_boundaries(
        self, reader: PdfReader, items: list, total_pages: int
    ) -> list[tuple[int, int, str]]:
        chapters = []
        for index in range(len(items)):
            boundary = self._chapter_boundary(
                reader, items, index, total_pages
            )
            if boundary is not None:
                chapters.append(boundary)
        return chapters

    def _chapter_boundary(
        self, reader: PdfReader, items: list, index: int, total_pages: int
    ) -> tuple[int, int, str] | None:
        item = self._chapter_item(items, index)
        if item is None:
            return None
        start = reader.get_page_number(item["/Page"])
        if start is None:
            return None
        end = self._chapter_end(reader, items, index, total_pages)
        title = str(item.get("/Title", f"Chapter {index + 1}"))
        return (start, end, title) if end > start else None

    @staticmethod
    def _chapter_item(items: list, index: int) -> dict | None:
        item = items[index]
        return item if isinstance(item, dict) and "/Page" in item else None

    @staticmethod
    def _chapter_end(
        reader: PdfReader, items: list, index: int, total_pages: int
    ) -> int:
        if index + 1 >= len(items):
            return total_pages
        next_item = items[index + 1]
        if not isinstance(next_item, dict) or "/Page" not in next_item:
            return total_pages
        next_end = reader.get_page_number(next_item["/Page"])
        return total_pages if next_end is None else next_end

    def _flatten_outline(self, outline: list, depth: int = 0) -> list:
        """Flatten nested outline structure."""
        flat = []
        for item in outline:
            if isinstance(item, list):
                flat.extend(self._flatten_outline(item, depth + 1))
            else:
                flat.append(item)
        return flat

    def needs_chunking(
        self, pdf_path: Path, threshold: int = DEFAULT_THRESHOLD
    ) -> bool:
        """Check if PDF needs chunking based on page count."""
        if not self._has_pypdf:
            return False

        page_count = self.count_pages(pdf_path)
        logger.info(f"PDF {pdf_path.name}: {page_count} pages")

        if page_count == 0:
            return False

        return page_count > threshold

    def chunk_pdf(
        self,
        pdf_path: Path,
        output_dir: Path,
        use_chapters: bool = True,
    ) -> Generator[Path]:
        """Split PDF into chunks and yield paths to chunk files.

        Uses chapter boundaries when available and requested, otherwise falls back
        to fixed-size chunks with overlap.
        """
        if not self._has_pypdf:
            yield pdf_path
            return

        from pypdf.errors import PdfReadError

        try:
            self._validate_pdf_file(pdf_path)
            if use_chapters:
                chapters = self.get_chapter_boundaries(pdf_path)
                if chapters:
                    yield from self._chunk_by_chapters(
                        pdf_path, output_dir, chapters, use_overlap=False
                    )
                    return
                logger.info(
                    "No chapter outline found, using fixed-size chunking with overlap"
                )

            yield from self._chunk_fixed_size_with_overlap(
                pdf_path, output_dir
            )
        except (OSError, ImportError, PdfReadError, RuntimeError) as e:
            logger.error(f"Failed to chunk invalid PDF {pdf_path}: {e}")
            return

    SKIP_CHAPTER_PATTERNS: ClassVar[tuple[str, ...]] = (
        "copyright",
        "table of contents",
        "toc",
        "preface",
        "acknowledgments",
        "index",
        "bibliography",
        "about the author",
        "about the authors",
        "foreword",
        "dedication",
        "about the reviewer",
        "about the technical reviewer",
        "who this book is for",
        "what this book covers",
        "to get the most out of this book",
        "conventions used",
        "get in touch",
        "share your thoughts",
        "download a free pdf",
        " Errata ",
        " piracy ",
        "questions",
        "why subscribe",
        "other books you may enjoy",
        "packt.com",
        "packtpub.com",
    )

    def _is_relevant_chapter(self, title: str) -> bool:
        title_lower = title.lower()
        return not any(
            pattern in title_lower for pattern in self.SKIP_CHAPTER_PATTERNS
        )

    def _chunk_by_chapters(
        self,
        pdf_path: Path,
        output_dir: Path,
        chapters: list[tuple[int, int, str]],
        use_overlap: bool = False,
    ) -> Generator[Path]:
        yield from self._chunk_by_chapters_core(
            pdf_path, output_dir, chapters, use_overlap
        )

    def _chunk_by_chapters_core(
        self,
        pdf_path: Path,
        output_dir: Path,
        chapters: list[tuple[int, int, str]],
        use_overlap: bool = False,
    ) -> Generator[Path]:
        from pypdf import PdfWriter

        reader = self._create_reader(pdf_path)
        try:
            total_pages = len(reader.pages)
            self._validate_page_count(pdf_path, total_pages)
            output_dir.mkdir(parents=True, exist_ok=True)
            accumulator = self._initial_accumulator(
                reader, chapters, total_pages, PdfWriter()
            )
            chunk_writers: list[_ChapterChunk] = []
            filtered_chunks_count = 0
            for ch_start, ch_end, ch_title in chapters:
                chapter_start = min(max(ch_start, 0), total_pages)
                chapter_end = min(max(ch_end, chapter_start), total_pages)
                chapter_pages = chapter_end - chapter_start
                is_relevant = self._is_relevant_chapter(ch_title)
                finalized, accumulator, filtered = (
                    self._rollover_chapter_chunk(
                        reader,
                        accumulator,
                        chapter_start,
                        chapter_pages,
                        use_overlap,
                    )
                )
                if finalized is not None:
                    chunk_writers.append(finalized)
                filtered_chunks_count += filtered
                self._append_chapter(
                    reader,
                    accumulator,
                    chapter_start,
                    chapter_end,
                    ch_title,
                    is_relevant,
                )

            finalized, filtered = self._finish_chapter_chunk(accumulator)
            if finalized is not None:
                chunk_writers.append(finalized)
            filtered_chunks_count += filtered
            self._log_filtered_chapters(filtered_chunks_count, chunk_writers)
            yield from self._write_chapter_chunks(
                pdf_path, output_dir, chunk_writers
            )
        finally:
            with contextlib.suppress(OSError):
                reader.stream.close()

    @staticmethod
    def _initial_accumulator(
        reader: PdfReader,
        chapters: list[tuple[int, int, str]],
        total_pages: int,
        writer: PdfWriter,
    ) -> _ChapterAccumulator:
        """Create the initial writer and copy pages before the first chapter."""
        accumulator = _ChapterAccumulator(writer)
        if not chapters:
            return accumulator
        prefix_end = min(max(chapters[0][0], 0), total_pages)
        for page_num in range(prefix_end):
            writer.add_page(reader.pages[page_num])
        accumulator.end = prefix_end
        accumulator.pages = prefix_end
        return accumulator

    def _rollover_chapter_chunk(
        self,
        reader: PdfReader,
        accumulator: _ChapterAccumulator,
        chapter_start: int,
        chapter_pages: int,
        use_overlap: bool,
    ) -> tuple[_ChapterChunk | None, _ChapterAccumulator, int]:
        """Finalize a full accumulator and create the next one."""
        if (
            accumulator.pages == 0
            or accumulator.pages + chapter_pages <= self.chunk_size
        ):
            return None, accumulator, 0

        finalized, filtered = self._finish_chapter_chunk(accumulator)
        next_accumulator = self._new_accumulator(
            reader, accumulator, chapter_start, use_overlap
        )
        return finalized, next_accumulator, filtered

    def _new_accumulator(
        self,
        reader: PdfReader,
        previous: _ChapterAccumulator,
        chapter_start: int,
        use_overlap: bool,
    ) -> _ChapterAccumulator:
        """Create a fresh accumulator, optionally copying relevant overlap."""
        from pypdf import PdfWriter

        accumulator = _ChapterAccumulator(PdfWriter())
        previous_relevant = previous.relevant_titles
        if use_overlap and previous_relevant:
            overlap_start = max(0, previous.end - self.overlap_pages)
            for page_num in range(overlap_start, previous.end):
                accumulator.writer.add_page(reader.pages[page_num])
            accumulator.start = overlap_start
            accumulator.pages = previous.end - overlap_start
            accumulator.end = previous.end
            return accumulator

        accumulator.start = chapter_start
        accumulator.end = chapter_start
        return accumulator

    @staticmethod
    def _append_chapter(
        reader: PdfReader,
        accumulator: _ChapterAccumulator,
        chapter_start: int,
        chapter_end: int,
        title: str,
        is_relevant: bool,
    ) -> None:
        """Append chapter pages and record its title once."""
        for page_num in range(chapter_start, chapter_end):
            accumulator.writer.add_page(reader.pages[page_num])
        if title not in accumulator.titles:
            accumulator.titles.append(title)
            if is_relevant:
                accumulator.relevant_titles.append(title)
        accumulator.end = max(accumulator.end, chapter_end)
        accumulator.pages += chapter_end - chapter_start

    @staticmethod
    def _finish_chapter_chunk(
        accumulator: _ChapterAccumulator,
    ) -> tuple[_ChapterChunk | None, int]:
        """Return a retained chunk or record one filtered irrelevant chunk."""
        if accumulator.relevant_titles:
            return (
                _ChapterChunk(
                    accumulator.writer,
                    accumulator.titles.copy(),
                    accumulator.start,
                    accumulator.end,
                    accumulator.relevant_titles.copy(),
                ),
                0,
            )
        logger.debug(
            f"Filtered chunk with pages {accumulator.start + 1}-"
            f"{accumulator.end}: only irrelevant chapters "
            f"({', '.join(accumulator.titles[:3])})"
        )
        return None, 1

    @staticmethod
    def _log_filtered_chapters(
        filtered_count: int, chunks: list[_ChapterChunk]
    ) -> None:
        """Log the number of discarded chapter-only chunks."""
        if filtered_count > 0:
            logger.info(
                f"Filtered out {filtered_count} chunks containing only "
                f"irrelevant chapters ({len(chunks)} chunks retained)"
            )

    @staticmethod
    def _write_chapter_chunks(
        pdf_path: Path,
        output_dir: Path,
        chunks: list[_ChapterChunk],
    ) -> Generator[Path]:
        """Write finalized chapter chunks and yield their paths."""
        for index, chunk in enumerate(chunks, 1):
            chunk_path = output_dir / f"{pdf_path.stem}_chunk_{index:03d}.pdf"
            with open(chunk_path, "wb") as output_file:
                chunk.writer.write(output_file)
            chapter_info = (
                f" (chapters: {', '.join(chunk.titles[:3])})"
                if chunk.titles
                else ""
            )
            logger.info(
                f"Created chunk {index}/{len(chunks)}: "
                f"pages {chunk.start + 1}-{chunk.end}{chapter_info}"
            )
            yield chunk_path

    def _chunk_fixed_size_with_overlap(
        self, pdf_path: Path, output_dir: Path
    ) -> Generator[Path]:
        """Split PDF into fixed-size chunks with overlap."""
        from pypdf import PdfWriter

        reader = self._create_reader(pdf_path)
        try:
            total_pages = len(reader.pages)
            self._validate_page_count(pdf_path, total_pages)
            if total_pages == 0:
                return

            stride = self.chunk_size - self.overlap_pages
            if total_pages <= self.chunk_size:
                num_chunks = 1
            else:
                num_chunks = (
                    1 + (total_pages - self.chunk_size + stride - 1) // stride
                )

            logger.info(
                f"Splitting {pdf_path.name} ({total_pages} pages) "
                f"into {num_chunks} chunks with {self.overlap_pages} pages overlap"
            )

            output_dir.mkdir(parents=True, exist_ok=True)

            for chunk_idx in range(num_chunks):
                start_page = chunk_idx * stride
                end_page = min(start_page + self.chunk_size, total_pages)
                writer = PdfWriter()
                for page_num in range(start_page, end_page):
                    writer.add_page(reader.pages[page_num])

                chunk_filename = (
                    f"{pdf_path.stem}_chunk_{chunk_idx + 1:03d}.pdf"
                )
                chunk_path = output_dir / chunk_filename

                with open(chunk_path, "wb") as output_file:
                    writer.write(output_file)

                overlap_info = (
                    f" (+{self.overlap_pages} overlap)"
                    if chunk_idx > 0
                    else ""
                )
                msg = (
                    f"Created chunk {chunk_idx + 1}/{num_chunks}: "
                    f"pages {start_page + 1}-{end_page}{overlap_info}"
                )
                logger.info(msg)
                yield chunk_path
        finally:
            with contextlib.suppress(OSError):
                reader.stream.close()

    def cleanup_chunks(self, chunks: list[Path]) -> None:
        """Delete temporary chunk files."""
        for chunk_path in chunks:
            try:
                if "_chunk_" in chunk_path.name:
                    chunk_path.unlink(missing_ok=True)
                    logger.debug(f"Deleted chunk: {chunk_path.name}")
            except OSError as e:
                logger.warning(f"Failed to delete chunk {chunk_path}: {e}")
