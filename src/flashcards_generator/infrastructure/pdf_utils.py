"""PDF manipulation utilities for chunking large files."""

from __future__ import annotations

import contextlib
import subprocess
from typing import TYPE_CHECKING

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

    DEFAULT_CHUNK_SIZE = 100
    DEFAULT_THRESHOLD = 150

    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.chunk_size = chunk_size
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

    def chunk_pdf(self, pdf_path: Path, output_dir: Path) -> Generator[Path]:
        """Split PDF into chunks and yield paths to chunk files."""
        if not self._has_pypdf:
            yield pdf_path
            return

        reader = None
        try:
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)
            num_chunks = (total_pages + self.chunk_size - 1) // self.chunk_size

            logger.info(
                f"Splitting {pdf_path.name} ({total_pages} pages) "
                f"into {num_chunks} chunks"
            )

            output_dir.mkdir(parents=True, exist_ok=True)

            for chunk_idx in range(num_chunks):
                start_page = chunk_idx * self.chunk_size
                end_page = min(start_page + self.chunk_size, total_pages)

                writer = PdfWriter()
                for page_num in range(start_page, end_page):
                    writer.add_page(reader.pages[page_num])

                chunk_filename = f"{pdf_path.stem}_chunk_{chunk_idx + 1:03d}.pdf"
                chunk_path = output_dir / chunk_filename

                with open(chunk_path, "wb") as output_file:
                    writer.write(output_file)

                msg = (
                    f"Created chunk {chunk_idx + 1}/{num_chunks}: "
                    f"pages {start_page + 1}-{end_page}"
                )
                logger.info(msg)
                yield chunk_path

        except (OSError, ImportError, RuntimeError) as e:
            logger.error(f"Failed to chunk PDF {pdf_path}: {e}")
            yield pdf_path
        finally:
            if reader is not None:
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
