from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from flashcards_generator.integrations.logging_config import get_logger

if TYPE_CHECKING:
    from pypdf import PdfReader

logger = get_logger("pdf_utils")


class FixedSizePDFChunkWriter:
    def __init__(self, chunk_size: int, overlap_pages: int) -> None:
        self.chunk_size = chunk_size
        self.overlap_pages = overlap_pages

    def write(
        self, reader: PdfReader, pdf_path: Path, output_dir: Path
    ) -> Generator[Path]:
        from pypdf import PdfWriter

        total_pages = len(reader.pages)
        if total_pages == 0:
            return

        stride = self.chunk_size - self.overlap_pages
        num_chunks = min(
            total_pages,
            max(
                1,
                (total_pages - self.overlap_pages + stride - 1) // stride,
            ),
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

            chunk_path = output_dir / (
                f"{pdf_path.stem}_chunk_{chunk_idx + 1:03d}.pdf"
            )
            with open(chunk_path, "wb") as output_file:
                writer.write(output_file)

            overlap_info = (
                f" (+{self.overlap_pages} overlap)" if chunk_idx > 0 else ""
            )
            logger.info(
                f"Created chunk {chunk_idx + 1}/{num_chunks}: "
                f"pages {start_page + 1}-{end_page}{overlap_info}"
            )
            yield chunk_path
