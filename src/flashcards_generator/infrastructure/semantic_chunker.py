"""Semantic chunking utilities using token-based segmentation."""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sklearn.feature_extraction.text import TfidfVectorizer

from flashcards_generator.infrastructure.document_limits import (
    MAX_PDF_FILE_BYTES as DEFAULT_MAX_PDF_FILE_BYTES,
)
from flashcards_generator.infrastructure.document_limits import (
    MAX_PDF_PAGE_TEXT_CHARS as DEFAULT_MAX_PDF_PAGE_TEXT_CHARS,
)
from flashcards_generator.infrastructure.document_limits import (
    MAX_PDF_PAGES as DEFAULT_MAX_PDF_PAGES,
)
from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable
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

        def __getitem__(self, index: slice) -> SimilarityMatrix: ...

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


class SemanticChunker:
    """Chunk PDF content based on tokens and semantic boundaries."""

    MAX_PDF_FILE_BYTES: ClassVar[int] = DEFAULT_MAX_PDF_FILE_BYTES
    MAX_PDF_PAGES: ClassVar[int] = DEFAULT_MAX_PDF_PAGES
    MAX_PDF_PAGE_TEXT_CHARS: ClassVar[int] = DEFAULT_MAX_PDF_PAGE_TEXT_CHARS
    DEFAULT_TARGET_TOKENS = 500
    DEFAULT_MIN_TOKENS = 200
    DEFAULT_MAX_TOKENS = 800
    DEFAULT_OVERLAP_TOKENS = 50

    SENTENCE_ENDINGS = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
    PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

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
        if file_size > self.MAX_PDF_FILE_BYTES:
            raise ValueError(
                f"PDF exceeds maximum size of "
                f"{self.MAX_PDF_FILE_BYTES} bytes: {pdf_path}"
            )

    def _validate_page_count(self, pdf_path: Path, page_count: int) -> None:
        """Reject PDFs whose page count could exhaust memory."""
        if page_count > self.MAX_PDF_PAGES:
            raise ValueError(
                f"PDF exceeds maximum page count of "
                f"{self.MAX_PDF_PAGES}: {pdf_path}"
            )

    def _extract_pdf_segments(
        self, reader: PdfReader, pdf_path: Path
    ) -> list[TextSegment]:
        segments = []
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
        if len(text) > self.MAX_PDF_PAGE_TEXT_CHARS:
            raise ValueError(
                f"PDF page text exceeds maximum size of "
                f"{self.MAX_PDF_PAGE_TEXT_CHARS} characters: {pdf_path}"
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
        return [s.strip() for s in sentences if s.strip()]

    def find_semantic_boundaries(
        self, segments: list[TextSegment]
    ) -> list[int]:
        """Find semantic boundaries using TF-IDF similarity."""
        if len(segments) < 3:
            return list(range(1, len(segments)))

        texts = [seg.text for seg in segments]
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

        for idx, (text, start_page, end_page) in enumerate(chunks, 1):
            token_count = self.token_counter.count(text)
            logger.info(
                f"Created semantic chunk {idx}/{len(chunks)}: "
                f"pages {start_page}-{end_page}, {token_count} tokens"
            )
            yield (text, start_page, end_page)

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


class QualityFilter:
    """Filter low-quality or trivial flashcards."""

    TRIVIAL_WORDS: ClassVar[set[str]] = {
        "is",
        "are",
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "it",
        "this",
        "that",
        "these",
        "those",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
    }

    MIN_CONTENT_WORDS = 3
    MAX_SIMILARITY = 0.85
    MAX_SIMILAR_PAIRS = 100_000

    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(
            max_features=50, stop_words="english"
        )

    def is_trivial(self, front: str, back: str) -> bool:
        """Check if flashcard is too trivial."""
        # Check if cloze is only trivial words
        words = front.lower().split()
        content_words = [w for w in words if w not in self.TRIVIAL_WORDS]

        if len(content_words) < self.MIN_CONTENT_WORDS:
            return True

        # Check for very short back
        if len(back.split()) < 2:
            return True

        # Check for subjective/evaluative language
        subjective_words = {
            "good",
            "bad",
            "better",
            "worse",
            "best",
            "worst",
            "important",
            "useful",
            "powerful",
            "obsolete",
        }
        return any(word in front.lower() for word in subjective_words)

    def find_similar_cards(
        self, cards: list[tuple[str, str]], threshold: float = MAX_SIMILARITY
    ) -> list[tuple[int, int, float]]:
        """Find similar cards based on front content."""
        if len(cards) < 2:
            return []

        fronts = [card[0] for card in cards]
        exact_pairs = self._exact_pairs(fronts)

        tfidf_matrix = self._fit_similarity_matrix(fronts)
        if tfidf_matrix is None:
            return [(i, j, 1.0) for i, j in sorted(exact_pairs)]

        similar_pairs, truncated = self._collect_similar_pairs(
            tfidf_matrix, exact_pairs, len(cards), threshold
        )

        self._warn_if_truncated(truncated)
        return self._sorted_similarity_pairs(similar_pairs)

    @staticmethod
    def _warn_if_truncated(truncated: bool) -> None:
        if truncated:
            logger.warning(
                "Similarity analysis reached the maximum pair limit: "
                f"{max(QualityFilter.MAX_SIMILAR_PAIRS, 0)}"
            )

    @staticmethod
    def _sorted_similarity_pairs(
        pairs: dict[tuple[int, int], float],
    ) -> list[tuple[int, int, float]]:
        return [(i, j, value) for (i, j), value in sorted(pairs.items())]

    @staticmethod
    def _as_similarity_matrix(matrix: SimilarityMatrix) -> SimilarityMatrix:
        return matrix

    def _fit_similarity_matrix(
        self, fronts: list[str]
    ) -> SimilarityMatrix | None:
        try:
            return self._as_similarity_matrix(
                self.vectorizer.fit_transform(fronts)
            )
        # An empty vocabulary still has deterministic exact-duplicate results.
        except ValueError as e:
            logger.warning(f"Similarity analysis failed: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Similarity analysis failed: {e}")
        return None

    @staticmethod
    def _exact_pairs(fronts: list[str]) -> set[tuple[int, int]]:
        pairs: set[tuple[int, int]] = set()
        first_indexes: dict[str, int] = {}
        for index, front in enumerate(fronts):
            normalized = " ".join(front.casefold().split())
            if normalized in first_indexes:
                pairs.add((first_indexes[normalized], index))
            else:
                first_indexes[normalized] = index
        return pairs

    def _collect_similar_pairs(
        self,
        matrix: SimilarityMatrix,
        exact_pairs: set[tuple[int, int]],
        card_count: int,
        threshold: float,
    ) -> tuple[dict[tuple[int, int], float], bool]:
        max_pairs = max(self.MAX_SIMILAR_PAIRS, 0)
        pairs = {pair: 1.0 for pair in sorted(exact_pairs)[:max_pairs]}
        truncated = len(exact_pairs) > max_pairs
        for start in range(card_count - 1):
            if truncated or len(pairs) >= max_pairs:
                return pairs, True
            similarities = (
                matrix.getrow(start) @ matrix[start + 1 :].T
            ).tocoo()
            truncated = self._add_similarity_row(
                pairs, similarities, start, threshold, max_pairs
            )
        return pairs, truncated

    @staticmethod
    def _add_similarity_row(
        pairs: dict[tuple[int, int], float],
        similarities: SimilarityCoordinates,
        start: int,
        threshold: float,
        max_pairs: int,
    ) -> bool:
        for column, similarity in zip(similarities.col, similarities.data):
            if similarity >= threshold:
                pairs.setdefault(
                    (start, start + 1 + int(column)), float(similarity)
                )
            if len(pairs) >= max_pairs:
                return True
        return False

    def filter_deck(
        self, cards: list[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]], dict[str, int]]:
        """Filter deck removing trivial and similar cards.

        Returns filtered cards and statistics.
        """
        stats = {"trivial_removed": 0, "similar_removed": 0, "kept": 0}

        # Remove trivial cards
        non_trivial = []
        for front, back in cards:
            if self.is_trivial(front, back):
                stats["trivial_removed"] += 1
            else:
                non_trivial.append((front, back))

        # Find and remove similar cards
        similar_pairs = self.find_similar_cards(non_trivial)
        to_remove = set()

        for _i, j, _ in similar_pairs:
            # Remove the second card of each similar pair
            to_remove.add(j)

        filtered = []
        for idx, card in enumerate(non_trivial):
            if idx not in to_remove:
                filtered.append(card)
            else:
                stats["similar_removed"] += 1

        stats["kept"] = len(filtered)
        logger.info(
            f"Quality filter: removed {stats['trivial_removed']} trivial, "
            f"{stats['similar_removed']} similar, kept {stats['kept']}"
        )

        return filtered, stats
