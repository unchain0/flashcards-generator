"""Semantic chunking utilities using token-based segmentation."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from flashcards_generator.engines.quality import QualityFilter
from flashcards_generator.integrations.document_limits import (
    MAX_PDF_FILE_BYTES as DEFAULT_MAX_PDF_FILE_BYTES,
)
from flashcards_generator.integrations.document_limits import (
    MAX_PDF_PAGE_TEXT_CHARS as DEFAULT_MAX_PDF_PAGE_TEXT_CHARS,
)
from flashcards_generator.integrations.document_limits import (
    MAX_PDF_PAGES as DEFAULT_MAX_PDF_PAGES,
)
from flashcards_generator.integrations.logging_config import get_logger
from flashcards_generator.integrations.semantic_analysis import (
    PDFTextLimits,
    SemanticAnalysis,
    TextSegment,
    TokenCounter,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


logger = get_logger("semantic_chunker")

__all__ = ["QualityFilter", "SemanticChunker", "TextSegment", "TokenCounter"]


class SemanticChunker:
    """Chunk PDF content based on tokens and semantic boundaries."""

    MAX_PDF_FILE_BYTES: ClassVar[int] = DEFAULT_MAX_PDF_FILE_BYTES
    MAX_PDF_PAGES: ClassVar[int] = DEFAULT_MAX_PDF_PAGES
    MAX_PDF_PAGE_TEXT_CHARS: ClassVar[int] = DEFAULT_MAX_PDF_PAGE_TEXT_CHARS
    DEFAULT_TARGET_TOKENS = 500
    DEFAULT_MIN_TOKENS = 200
    DEFAULT_MAX_TOKENS = 800
    DEFAULT_OVERLAP_TOKENS = 50

    SENTENCE_ENDINGS = SemanticAnalysis.SENTENCE_ENDINGS
    PARAGRAPH_BREAK = SemanticAnalysis.PARAGRAPH_BREAK

    def __init__(
        self,
        target_tokens: int = DEFAULT_TARGET_TOKENS,
        min_tokens: int = DEFAULT_MIN_TOKENS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ):
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.token_counter = TokenCounter()
        self._semantic_analysis = SemanticAnalysis(
            self.token_counter,
            PDFTextLimits(
                max_file_bytes=self.MAX_PDF_FILE_BYTES,
                max_pages=self.MAX_PDF_PAGES,
                max_page_text_chars=self.MAX_PDF_PAGE_TEXT_CHARS,
            ),
        )

    def extract_text_from_pdf(self, pdf_path: Path) -> list[TextSegment]:
        """Extract text from PDF with page tracking."""
        return self._semantic_analysis.extract_text_from_pdf(pdf_path)

    def split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        return self._semantic_analysis.split_into_sentences(text)

    def find_semantic_boundaries(
        self, segments: list[TextSegment]
    ) -> list[int]:
        """Find semantic boundaries using TF-IDF similarity."""
        return self._semantic_analysis.find_semantic_boundaries(segments)

    def create_semantic_chunks(
        self, pdf_path: Path
    ) -> Generator[tuple[str, int, int]]:
        """Create chunks respecting semantic boundaries and token limits.

        Yields tuples of (chunk_text, start_page, end_page).
        """
        segments = self.extract_text_from_pdf(pdf_path)
        if not segments:
            return

        boundaries = self.find_semantic_boundaries(segments)
        chunks = self._build_chunks(segments, boundaries)

        for idx, (text, start_page, end_page) in enumerate(chunks, 1):
            token_count = self.token_counter.count(text)
            logger.info(
                f"Created semantic chunk {idx}/{len(chunks)}: "
                f"pages {start_page}-{end_page}, {token_count} tokens"
            )
            yield (text, start_page, end_page)

    def _build_chunks(
        self, segments: list[TextSegment], boundaries: list[int]
    ) -> list[tuple[str, int, int]]:
        chunks: list[tuple[str, int, int]] = []
        current_chunk: list[tuple[str, int, int]] = []
        current_chunk_tokens = 0

        for i, segment in enumerate(segments):
            current_chunk = self._add_segment(segment, current_chunk, chunks)
            current_chunk_tokens = self._chunk_token_count(current_chunk)

            if i in boundaries and current_chunk_tokens >= self.target_tokens:
                self._append_chunk(chunks, current_chunk)
                current_chunk = []
                current_chunk_tokens = 0

        self._append_chunk(chunks, current_chunk)
        return chunks

    def _chunk_token_count(self, chunk: list[tuple[str, int, int]]) -> int:
        return self.token_counter.count(" ".join(text for text, _, _ in chunk))

    @staticmethod
    def _append_chunk(
        chunks: list[tuple[str, int, int]],
        current: list[tuple[str, int, int]],
    ) -> None:
        if current:
            chunks.append((
                " ".join(text for text, _, _ in current),
                current[0][1],
                current[-1][2],
            ))

    def _add_segment(
        self,
        segment: TextSegment,
        current: list[tuple[str, int, int]],
        chunks: list[tuple[str, int, int]],
    ) -> list[tuple[str, int, int]]:
        for sentence in self.split_into_sentences(segment.text):
            for piece in self._split_to_max_tokens(sentence):
                item = (piece, segment.start_page, segment.end_page)
                candidate = [*current, item]
                if self._chunk_token_count(candidate) > self.max_tokens:
                    self._append_chunk(chunks, current)
                    current = self._overflow_chunk(current, item)
                else:
                    current = candidate
        return current

    def _overflow_chunk(
        self,
        current: list[tuple[str, int, int]],
        item: tuple[str, int, int],
    ) -> list[tuple[str, int, int]]:
        budget = min(
            self.overlap_tokens,
            self.max_tokens - self.token_counter.count(item[0]),
        )
        overflow = [*self._get_overlap_segments(current, budget), item]
        while (
            len(overflow) > 1
            and self._chunk_token_count(overflow) > self.max_tokens
        ):
            overflow.pop(0)
        return overflow

    def _split_to_max_tokens(self, text: str) -> list[str]:
        """Split text into nonempty pieces that fit the configured maximum."""
        if self.token_counter.count(text) <= self.max_tokens:
            return [text]

        pieces: list[str] = []
        current_words: list[str] = []
        for word in text.split():
            pieces, current_words = self._add_word_piece(
                word, pieces, current_words
            )

        if current_words:
            pieces.append(" ".join(current_words))
        return pieces

    def _add_word_piece(
        self, word: str, pieces: list[str], current_words: list[str]
    ) -> tuple[list[str], list[str]]:
        if self.token_counter.count(word) > self.max_tokens:
            if current_words:
                pieces.append(" ".join(current_words))
            return pieces + self._split_long_word(word), []
        candidate = " ".join([*current_words, word])
        if (
            current_words
            and self.token_counter.count(candidate) > self.max_tokens
        ):
            return pieces + [" ".join(current_words)], [word]
        return pieces, [*current_words, word]

    def _split_long_word(self, word: str) -> list[str]:
        """Split a tokenized word when it alone exceeds the maximum."""
        encoding = getattr(self.token_counter, "encoding", None)
        if encoding is not None:
            token_ids = encoding.encode(word)
            return [
                encoding.decode(token_ids[start : start + self.max_tokens])
                for start in range(0, len(token_ids), self.max_tokens)
            ]
        return self._split_long_word_by_character(word)

    def _split_long_word_by_character(self, word: str) -> list[str]:
        pieces: list[str] = []
        current = ""
        for character in word:
            candidate = f"{current}{character}"
            if (
                current
                and self.token_counter.count(candidate) > self.max_tokens
            ):
                pieces.append(current)
                current = character
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces

    def _get_overlap_segments(
        self,
        previous_chunk: list[tuple[str, int, int]],
        token_budget: int,
    ) -> list[tuple[str, int, int]]:
        """Get the trailing page-aware overlap that fits ``token_budget``."""
        overlap: list[tuple[str, int, int]] = []
        overlap_tokens = 0
        for item in reversed(previous_chunk):
            sentence_tokens = self.token_counter.count(item[0])
            if overlap_tokens + sentence_tokens <= token_budget:
                overlap.insert(0, item)
                overlap_tokens += sentence_tokens
            else:
                break
        return overlap

    def _get_overlap_text(self, previous_chunk_text: list[str]) -> list[str]:
        """Get overlap text from previous chunk."""
        overlap_text: list[str] = []
        overlap_tokens = 0

        for sentence in reversed(previous_chunk_text):
            sentence_tokens = self.token_counter.count(sentence)
            if overlap_tokens + sentence_tokens <= self.overlap_tokens:
                overlap_text.insert(0, sentence)
                overlap_tokens += sentence_tokens
            else:
                break

        return overlap_text
