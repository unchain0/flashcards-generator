from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class PDFChunkerPort(Protocol):
    def needs_chunking(self, pdf_path: Path, threshold: int) -> bool: ...

    def chunk_pdf(
        self,
        pdf_path: Path,
        output_dir: Path,
        use_chapters: bool = True,
    ) -> Iterable[Path]: ...

    def cleanup_chunks(self, chunks: list[Path]) -> None: ...
