"""Semantic chunking utilities using token-based segmentation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from flashcards_generator.infrastructure.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

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
            logger.warning("tiktoken not available, using word-based estimation")
            self._available = False

    def count(self, text: str) -> int:
        """Count tokens in text."""
        if self._available:
            return len(self.encoding.encode(text))
        # Fallback: estimate 1.5 tokens per word
        return int(len(text.split()) * 1.5)


class SemanticChunker:
    """Chunk PDF content based on tokens and semantic boundaries."""

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
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path), strict=False)
            segments = []

            for page_num, page in enumerate(reader.pages, 1):
                text = page.extract_text()
                if text.strip():
                    token_count = self.token_counter.count(text)
                    segments.append(
                        TextSegment(
                            text=text,
                            start_page=page_num,
                            end_page=page_num,
                            token_count=token_count,
                        )
                    )

            return segments
        except Exception as e:
            logger.error(f"Failed to extract text from {pdf_path}: {e}")
            return []

    def split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        sentences = self.SENTENCE_ENDINGS.split(text)
        return [s.strip() for s in sentences if s.strip()]

    def find_semantic_boundaries(self, segments: list[TextSegment]) -> list[int]:
        """Find semantic boundaries using TF-IDF similarity."""
        if len(segments) < 3:
            return list(range(1, len(segments)))

        texts = [seg.text for seg in segments]
        vectorizer = TfidfVectorizer(max_features=100, stop_words="english")

        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            similarities = cosine_similarity(tfidf_matrix)

            boundaries = []
            for i in range(1, len(segments) - 1):
                # Check if similarity with next segment is significantly lower
                prev_sim = similarities[i][i - 1]
                next_sim = similarities[i][i + 1]

                # Boundary if similarity drops significantly
                if prev_sim > 0.3 and next_sim < prev_sim * 0.7:
                    boundaries.append(i)

            return boundaries if boundaries else list(range(1, len(segments)))
        except Exception as e:
            logger.warning(f"Semantic analysis failed: {e}, using fixed intervals")
            return list(range(1, len(segments)))

    def create_semantic_chunks(self, pdf_path: Path) -> Generator[tuple[str, int, int]]:
        """Create chunks respecting semantic boundaries and token limits.

        Yields tuples of (chunk_text, start_page, end_page).
        """
        segments = self.extract_text_from_pdf(pdf_path)
        if not segments:
            return

        # Find semantic boundaries
        boundaries = self.find_semantic_boundaries(segments)

        chunks = []
        current_chunk_text = []
        current_chunk_tokens = 0
        current_start_page = segments[0].start_page
        current_end_page = segments[0].start_page

        for i, segment in enumerate(segments):
            sentences = self.split_into_sentences(segment.text)

            for sentence in sentences:
                sentence_tokens = self.token_counter.count(sentence)

                # Check if adding this sentence would exceed max tokens
                if current_chunk_tokens + sentence_tokens > self.max_tokens:
                    # Save current chunk
                    if current_chunk_tokens >= self.min_tokens:
                        chunks.append(
                            (
                                " ".join(current_chunk_text),
                                current_start_page,
                                current_end_page,
                            )
                        )

                    # Start new chunk with overlap
                    overlap_text = self._get_overlap_text(current_chunk_text)
                    current_chunk_text = [*overlap_text, sentence]
                    current_chunk_tokens = sum(
                        self.token_counter.count(t) for t in current_chunk_text
                    )
                    current_start_page = segment.start_page
                else:
                    current_chunk_text.append(sentence)
                    current_chunk_tokens += sentence_tokens

                current_end_page = segment.end_page

            # Check for semantic boundary
            if i in boundaries and current_chunk_tokens >= self.target_tokens:
                chunks.append(
                    (
                        " ".join(current_chunk_text),
                        current_start_page,
                        current_end_page,
                    )
                )
                current_chunk_text = []
                current_chunk_tokens = 0
                current_start_page = segment.start_page

        # Don't forget the last chunk
        if current_chunk_text and current_chunk_tokens >= self.min_tokens:
            chunks.append(
                (
                    " ".join(current_chunk_text),
                    current_start_page,
                    current_end_page,
                )
            )

        # Yield chunks with logging
        for idx, (text, start_page, end_page) in enumerate(chunks, 1):
            token_count = self.token_counter.count(text)
            logger.info(
                f"Created semantic chunk {idx}/{len(chunks)}: "
                f"pages {start_page}-{end_page}, {token_count} tokens"
            )
            yield (text, start_page, end_page)

    def _get_overlap_text(self, previous_chunk_text: list[str]) -> list[str]:
        """Get overlap text from previous chunk."""
        overlap_text = []
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

    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=50, stop_words="english")

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

        try:
            tfidf_matrix = self.vectorizer.fit_transform(fronts)
            similarities = cosine_similarity(tfidf_matrix)

            similar_pairs = []
            for i in range(len(cards)):
                for j in range(i + 1, len(cards)):
                    sim = similarities[i][j]
                    if sim >= threshold:
                        similar_pairs.append((i, j, float(sim)))

            return similar_pairs
        except Exception as e:
            logger.warning(f"Similarity analysis failed: {e}")
            return []

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
