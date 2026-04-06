"""PDF manipulation utilities for chunking large files."""

from __future__ import annotations

import contextlib
import subprocess
from typing import TYPE_CHECKING, ClassVar

from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

logger = get_logger("pdf_utils")


class PPTXConverter:
    """Converts PowerPoint (.pptx) files to PDF format."""

    def __init__(self) -> None:
        self._has_libreoffice = self._check_libreoffice()

    def _check_libreoffice(self) -> bool:
        """Check if LibreOffice is available."""
        try:
            result = subprocess.run(
                ["soffice", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired, FileNotFoundError:
            logger.warning("LibreOffice not found. PPTX conversion disabled.")
            return False

    def convert(self, pptx_path: Path, output_dir: Path) -> Path | None:
        """Convert PPTX to PDF using LibreOffice."""
        if not self._has_libreoffice:
            logger.error(f"Cannot convert {pptx_path.name}: LibreOffice not available")
            return None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)

            # Run LibreOffice conversion
            result = subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(pptx_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                logger.error(f"PPTX conversion failed: {result.stderr}")
                return None

            # LibreOffice creates PDF with same name but .pdf extension
            pdf_name = pptx_path.stem + ".pdf"
            pdf_path = output_dir / pdf_name

            if pdf_path.exists():
                logger.info(f"Converted {pptx_path.name} → {pdf_name}")
                return pdf_path
            else:
                logger.error(f"PDF not found after conversion: {pdf_path}")
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"PPTX conversion timeout: {pptx_path.name}")
            return None
        except OSError as e:
            logger.error(f"PPTX conversion error: {e}")
            return None


class PDFChunker:
    """Handles PDF page counting and chunking for large files."""

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

    def count_pages(self, pdf_path: Path) -> int:
        """Count pages in PDF file."""
        if not self._has_pypdf:
            return 0

        reader = None
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            return len(reader.pages)
        except (OSError, ImportError, RuntimeError) as e:
            logger.error(f"Failed to count pages in {pdf_path}: {e}")
            return 0
        finally:
            if reader is not None:
                with contextlib.suppress(OSError):
                    reader.stream.close()

    def get_chapter_boundaries(self, pdf_path: Path) -> list[tuple[int, int, str]]:
        """Extract chapter boundaries from PDF outline/bookmarks.

        Returns list of (start_page, end_page, title) tuples with 0-indexed pages.
        """
        if not self._has_pypdf:
            return []

        reader = None
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)

            if not reader.outline:
                return []

            chapters = []
            outline_items = self._flatten_outline(reader.outline)

            for i, item in enumerate(outline_items):
                if isinstance(item, dict) and "/Page" in item:
                    page_num = reader.get_page_number(item["/Page"])
                    title = str(item.get("/Title", f"Chapter {i + 1}"))

                    if i < len(outline_items) - 1:
                        next_item = outline_items[i + 1]
                        if isinstance(next_item, dict) and "/Page" in next_item:
                            end_page = reader.get_page_number(next_item["/Page"])
                        else:
                            end_page = total_pages
                    else:
                        end_page = total_pages

                    if end_page > page_num:
                        chapters.append((page_num, end_page, title))

            logger.info(f"Found {len(chapters)} chapters in {pdf_path.name}")
            return chapters
        except (OSError, ImportError, RuntimeError) as e:
            logger.warning(f"Failed to extract chapter boundaries from {pdf_path}: {e}")
            return []
        finally:
            if reader is not None:
                with contextlib.suppress(OSError):
                    reader.stream.close()

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

        if use_chapters:
            chapters = self.get_chapter_boundaries(pdf_path)
            if chapters:
                yield from self._chunk_by_chapters(
                    pdf_path, output_dir, chapters, use_overlap=False
                )
                return
            else:
                logger.info(
                    "No chapter outline found, using fixed-size chunking with overlap"
                )

        yield from self._chunk_fixed_size_with_overlap(pdf_path, output_dir)

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
    )

    def _is_relevant_chapter(self, title: str) -> bool:
        title_lower = title.lower()
        return not any(pattern in title_lower for pattern in self.SKIP_CHAPTER_PATTERNS)

    def _chunk_by_chapters(
        self,
        pdf_path: Path,
        output_dir: Path,
        chapters: list[tuple[int, int, str]],
        use_overlap: bool = False,
    ) -> Generator[Path]:
        """Create chunks respecting chapter boundaries."""
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(pdf_path))
        total_pages = len(reader.pages)

        output_dir.mkdir(parents=True, exist_ok=True)

        relevant_chapters = [
            (ch_start, ch_end, ch_title)
            for ch_start, ch_end, ch_title in chapters
            if self._is_relevant_chapter(ch_title)
        ]

        if not relevant_chapters:
            logger.warning("No relevant chapters found after filtering")
            relevant_chapters = chapters

        logger.info(
            f"Processing {len(relevant_chapters)} relevant chapters "
            f"({len(chapters) - len(relevant_chapters)} filtered out)"
        )

        current_chunk_start = 0
        current_chunk_pages = 0
        chapters_in_chunk: list[str] = []
        chunk_writers: list[tuple[PdfWriter, list[str], int, int]] = []
        current_writer = PdfWriter()

        for ch_start, ch_end, ch_title in relevant_chapters:
            chapter_pages = ch_end - ch_start

            # If adding this chapter would exceed chunk size, finalize current chunk
            if (
                current_chunk_pages > 0
                and current_chunk_pages + chapter_pages > self.chunk_size
            ):
                actual_end_page = current_chunk_start + current_chunk_pages
                chunk_writers.append(
                    (
                        current_writer,
                        chapters_in_chunk.copy(),
                        current_chunk_start,
                        actual_end_page,
                    )
                )

                current_writer = PdfWriter()
                chapters_in_chunk = []

                if use_overlap:
                    overlap_start = max(0, actual_end_page - self.overlap_pages)
                    for page_num in range(overlap_start, actual_end_page):
                        current_writer.add_page(reader.pages[page_num])
                    current_chunk_start = overlap_start
                    current_chunk_pages = actual_end_page - overlap_start
                else:
                    current_chunk_start = actual_end_page
                    current_chunk_pages = 0

            # Add chapter pages to current chunk
            for page_num in range(ch_start, min(ch_end, total_pages)):
                current_writer.add_page(reader.pages[page_num])

            if ch_title not in chapters_in_chunk:
                chapters_in_chunk.append(ch_title)
            current_chunk_pages += chapter_pages

        # Add the last chunk
        if current_chunk_pages > 0:
            actual_end_page = current_chunk_start + current_chunk_pages
            chunk_writers.append(
                (
                    current_writer,
                    chapters_in_chunk.copy(),
                    current_chunk_start,
                    actual_end_page,
                )
            )

        # Write chunks to files
        for i, (writer, ch_titles, start, end) in enumerate(chunk_writers, 1):
            chunk_filename = f"{pdf_path.stem}_chunk_{i:03d}.pdf"
            chunk_path = output_dir / chunk_filename

            with open(chunk_path, "wb") as output_file:
                writer.write(output_file)

            chapter_info = (
                f" (chapters: {', '.join(ch_titles[:3])})" if ch_titles else ""
            )
            msg = f"Created chunk {i}/{len(chunk_writers)}: "
            msg += f"pages {start + 1}-{end}{chapter_info}"
            logger.info(msg)
            yield chunk_path

    def _chunk_fixed_size_with_overlap(
        self, pdf_path: Path, output_dir: Path
    ) -> Generator[Path]:
        """Split PDF into fixed-size chunks with overlap."""
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(pdf_path))
        total_pages = len(reader.pages)
        effective_chunk_size = self.chunk_size - self.overlap_pages
        num_chunks = (total_pages + effective_chunk_size - 1) // effective_chunk_size

        logger.info(
            f"Splitting {pdf_path.name} ({total_pages} pages) "
            f"into {num_chunks} chunks with {self.overlap_pages} pages overlap"
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        for chunk_idx in range(num_chunks):
            effective_chunk_size = self.chunk_size - self.overlap_pages
            start_page = chunk_idx * effective_chunk_size

            if chunk_idx == 0:
                end_page = min(start_page + self.chunk_size, total_pages)
            else:
                start_page = max(0, start_page - self.overlap_pages)
                end_page = min(start_page + self.chunk_size, total_pages)

            writer = PdfWriter()
            for page_num in range(start_page, end_page):
                writer.add_page(reader.pages[page_num])

            chunk_filename = f"{pdf_path.stem}_chunk_{chunk_idx + 1:03d}.pdf"
            chunk_path = output_dir / chunk_filename

            with open(chunk_path, "wb") as output_file:
                writer.write(output_file)

            overlap_info = f" (+{self.overlap_pages} overlap)" if chunk_idx > 0 else ""
            msg = (
                f"Created chunk {chunk_idx + 1}/{num_chunks}: "
                f"pages {start_page + 1}-{end_page}{overlap_info}"
            )
            logger.info(msg)
            yield chunk_path

    def cleanup_chunks(self, chunks: list[Path]) -> None:
        """Delete temporary chunk files."""
        for chunk_path in chunks:
            try:
                if "_chunk_" in chunk_path.name:
                    chunk_path.unlink(missing_ok=True)
                    logger.debug(f"Deleted chunk: {chunk_path.name}")
            except OSError as e:
                logger.warning(f"Failed to delete chunk {chunk_path}: {e}")
