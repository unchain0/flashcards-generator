from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sklearn.feature_extraction.text import TfidfVectorizer

from flashcards_generator.integrations.document_limits import (
    MAX_PDF_FILE_BYTES,
    MAX_PDF_PAGE_TEXT_CHARS,
    MAX_PDF_PAGES,
)
from flashcards_generator.integrations.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path
    from typing import Protocol

    from pypdf import PdfReader

    class PDFPage(Protocol):
        def extract_text(self) -> str | None: ...

    class SimilarityCoordinates(Protocol):
        col: Iterable[int]
        data: Iterable[float]

    class SimilarityMatrix(Protocol):
        @property
        def T(self) -> SimilarityMatrix: ...

        def getrow(self, index: int) -> SimilarityMatrix: ...

        def __getitem__(self, index: slice, /) -> SimilarityMatrix: ...

        def multiply(self, other: SimilarityMatrix) -> SimilarityMatrix: ...

        def sum(self, axis: int) -> SimilarityTotals: ...

        def __matmul__(self, other: SimilarityMatrix) -> SimilarityProduct: ...

    class SimilarityProduct(Protocol):
        def tocoo(self) -> SimilarityCoordinates: ...

    class SimilarityTotals(Protocol):
        def __getitem__(self, index: tuple[int, int]) -> float: ...


logger = get_logger("semantic_chunker")


@dataclass
class TextSegment:
    """A segment of text with metadata."""

    text: str
    start_page: int
    end_page: int
    token_count: int
    is_sentence_boundary: bool = False


@dataclass(frozen=True)
class PDFTextLimits:
    max_file_bytes: int = MAX_PDF_FILE_BYTES
    max_pages: int = MAX_PDF_PAGES
    max_page_text_chars: int = MAX_PDF_PAGE_TEXT_CHARS


class TokenCounter:
    """Count tokens using tiktoken (cl100k_base encoding)."""

    def __init__(self) -> None:
        try:
            import tiktoken

            self.encoding = tiktoken.get_encoding("cl100k_base")
            self._available = True
        except ImportError:
            logger.warning(
                "tiktoken not available, using word-based estimation"
            )
            self._available = False

    def count(self, text: str) -> int:
        """Count tokens in text."""
        if self._available:
            return len(self.encoding.encode(text))
        # Fallback: estimate 1.5 tokens per word
        return int(len(text.split()) * 1.5)


class SemanticAnalysis:
    SENTENCE_ENDINGS = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
    PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

    def __init__(
        self, token_counter: TokenCounter, pdf_limits: PDFTextLimits
    ) -> None:
        self.token_counter = token_counter
        self.pdf_limits = pdf_limits

    def extract_text_from_pdf(self, pdf_path: Path) -> list[TextSegment]:
        """Extract text from PDF with page tracking."""
        reader = None
        try:
            from pypdf import PdfReader

            self._validate_pdf_path(pdf_path)
            reader = PdfReader(str(pdf_path), strict=False)
            self._validate_page_count(pdf_path, len(reader.pages))
            return self._extract_pdf_segments(reader, pdf_path)
        # PDF extraction is optional enrichment and must degrade to no segments.
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to extract text from {pdf_path}: {e}")
            return []
        finally:
            if reader is not None:
                stream = getattr(reader, "stream", None)
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()

    def _validate_pdf_path(self, pdf_path: Path) -> None:
        """Reject oversized PDF input before parsing."""
        try:
            file_size = pdf_path.stat().st_size
        except FileNotFoundError:
            return
        if file_size > self.pdf_limits.max_file_bytes:
            raise ValueError(
                f"PDF exceeds maximum size of "
                f"{self.pdf_limits.max_file_bytes} bytes: {pdf_path}"
            )

    def _validate_page_count(self, pdf_path: Path, page_count: int) -> None:
        """Reject PDFs whose page count could exhaust memory."""
        if page_count > self.pdf_limits.max_pages:
            raise ValueError(
                f"PDF exceeds maximum page count of "
                f"{self.pdf_limits.max_pages}: {pdf_path}"
            )

    def _extract_pdf_segments(
        self, reader: PdfReader, pdf_path: Path
    ) -> list[TextSegment]:
        segments: list[TextSegment] = []
        for page_num, page in enumerate(reader.pages, 1):
            segment = self._extract_page_segment(page, page_num, pdf_path)
            if segment is not None:
                segments.append(segment)
        return segments

    def _extract_page_segment(
        self, page: PDFPage, page_num: int, pdf_path: Path
    ) -> TextSegment | None:
        text = page.extract_text()
        if not text or not text.strip():
            return None
        if len(text) > self.pdf_limits.max_page_text_chars:
            raise ValueError(
                f"PDF page text exceeds maximum size of "
                f"{self.pdf_limits.max_page_text_chars} characters: {pdf_path}"
            )
        return TextSegment(
            text=text,
            start_page=page_num,
            end_page=page_num,
            token_count=self.token_counter.count(text),
        )

    def split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        sentences = self.SENTENCE_ENDINGS.split(text)
        return [sentence.strip() for sentence in sentences if sentence.strip()]

    def find_semantic_boundaries(
        self, segments: list[TextSegment]
    ) -> list[int]:
        """Find semantic boundaries using TF-IDF similarity."""
        if len(segments) < 3:
            return list(range(1, len(segments)))

        texts = [segment.text for segment in segments]
        vectorizer = TfidfVectorizer(max_features=100, stop_words="english")

        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            similarities = self._adjacent_similarity_scores(
                tfidf_matrix, len(segments)
            )
            boundaries = self._semantic_boundary_indexes(
                similarities, len(segments)
            )
            return boundaries if boundaries else list(range(1, len(segments)))
        # Semantic analysis must fall back when third-party vectorization fails.
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Semantic analysis failed: {e}, using fixed intervals"
            )
            return list(range(1, len(segments)))

    @staticmethod
    def _semantic_boundary_indexes(
        similarities: list[float], count: int
    ) -> list[int]:
        boundaries = []
        for index in range(1, count - 1):
            previous = similarities[index - 1]
            following = similarities[index]
            if previous > 0.3 and following < previous * 0.7:
                boundaries.append(index)
        return boundaries

    @staticmethod
    def _adjacent_similarity_scores(
        tfidf_matrix: SimilarityMatrix, count: int
    ) -> list[float]:
        """Compute only adjacent similarities without an all-pairs matrix."""
        if count < 2:
            return []
        adjacent_products = tfidf_matrix[:-1].multiply(tfidf_matrix[1:])
        totals = adjacent_products.sum(axis=1)
        return [float(totals[index, 0]) for index in range(count - 1)]
