"""Tests for semantic chunking functionality."""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from scipy.sparse import csr_matrix

from flashcards_generator.infrastructure.semantic_chunker import (
    QualityFilter,
    SemanticChunker,
    TextSegment,
    TokenCounter,
)


class TestTokenCounter:
    """Test TokenCounter class."""

    def test_count_with_text(self):
        """Test token counting with sample text."""
        counter = TokenCounter()
        text = "FastAPI is a modern web framework."
        count = counter.count(text)
        assert count > 0
        # Should be approximately 1.5x word count (fallback estimation)
        words = len(text.split())
        assert count >= words

    def test_count_empty_string(self):
        """Test token counting with empty string."""
        counter = TokenCounter()
        assert counter.count("") == 0


class TestSemanticChunker:
    """Test SemanticChunker class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        chunker = SemanticChunker()
        assert chunker.target_tokens == 500
        assert chunker.min_tokens == 200
        assert chunker.max_tokens == 800
        assert chunker.overlap_tokens == 50

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        chunker = SemanticChunker(
            target_tokens=300,
            min_tokens=100,
            max_tokens=600,
            overlap_tokens=30,
        )
        assert chunker.target_tokens == 300
        assert chunker.min_tokens == 100
        assert chunker.max_tokens == 600
        assert chunker.overlap_tokens == 30

    def test_split_into_sentences(self):
        """Test sentence splitting."""
        chunker = SemanticChunker()
        text = "First sentence. Second sentence! Third sentence?"
        sentences = chunker.split_into_sentences(text)
        assert len(sentences) == 3
        assert "First sentence" in sentences[0]
        assert "Second sentence" in sentences[1]
        assert "Third sentence" in sentences[2]

    def test_split_into_sentences_single(self):
        """Test sentence splitting with single sentence."""
        chunker = SemanticChunker()
        text = "Only one sentence."
        sentences = chunker.split_into_sentences(text)
        assert len(sentences) == 1
        assert "Only one sentence" in sentences[0]

    def test_find_semantic_boundaries_short_list(self):
        """Test boundary finding with short segment list."""
        chunker = SemanticChunker()
        segments = [
            TextSegment("First text", 1, 1, 10),
            TextSegment("Second text", 2, 2, 10),
        ]
        boundaries = chunker.find_semantic_boundaries(segments)
        # For less than 3 segments, should return range(1, len)
        assert boundaries == [1]

    def test_find_semantic_boundaries_empty(self):
        """Test boundary finding with empty list."""
        chunker = SemanticChunker()
        boundaries = chunker.find_semantic_boundaries([])
        assert boundaries == []

    def test_find_semantic_boundaries_does_not_materialize_dense_matrix(self):
        """Boundary detection must remain bounded for many segments."""
        chunker = SemanticChunker()
        segments = [
            TextSegment(f"segment {index}", 1, 1, 2) for index in range(4)
        ]
        sparse_vectors = csr_matrix([
            [1.0, 0.0, 0.0],
            [0.5, 0.8660254, 0.0],
            [0.0, 0.1, 0.9949874],
            [0.0, 0.0, 1.0],
        ])

        with (
            patch(
                "flashcards_generator.infrastructure.semantic_chunker."
                "TfidfVectorizer.fit_transform",
                return_value=sparse_vectors,
            ),
            patch(
                "scipy.sparse.csr_matrix.toarray",
                side_effect=AssertionError(
                    "dense similarity matrix was materialized"
                ),
            ),
        ):
            boundaries = chunker.find_semantic_boundaries(segments)

        assert boundaries == [1]

    def test_get_overlap_text(self):
        """Test overlap text extraction."""
        chunker = SemanticChunker(overlap_tokens=20)
        previous = ["Short sentence.", "Another short one."]
        overlap = chunker._get_overlap_text(previous)
        assert isinstance(overlap, list)


class TestQualityFilter:
    """Test QualityFilter class."""

    def test_init(self):
        """Test QualityFilter initialization."""
        filter_q = QualityFilter()
        assert hasattr(filter_q, "vectorizer")
        assert hasattr(filter_q, "TRIVIAL_WORDS")
        assert len(filter_q.TRIVIAL_WORDS) > 0

    def test_is_trivial_valid_card(self):
        """Test trivial detection with valid card."""
        filter_q = QualityFilter()
        front = "FastAPI is a {{c1::modern}} web framework."
        back = "A Python framework"
        assert not filter_q.is_trivial(front, back)

    def test_is_trivial_only_stopwords(self):
        """Test trivial detection with only stopwords."""
        filter_q = QualityFilter()
        front = "The {{c1::the}} is a word."
        back = "Article"
        assert filter_q.is_trivial(front, back)

    def test_is_trivial_short_back(self):
        """Test trivial detection with very short back."""
        filter_q = QualityFilter()
        front = "Python is a {{c1::language}}."
        back = "Yes"
        assert filter_q.is_trivial(front, back)

    def test_is_trivial_subjective(self):
        """Test trivial detection with subjective words."""
        filter_q = QualityFilter()
        front = "This is a very {{c1::good}} solution."
        back = "Positive"
        assert filter_q.is_trivial(front, back)

    def test_find_similar_cards_empty(self):
        """Test similarity finding with empty list."""
        filter_q = QualityFilter()
        result = filter_q.find_similar_cards([])
        assert result == []

    def test_find_similar_cards_single(self):
        """Test similarity finding with single card."""
        filter_q = QualityFilter()
        cards = [("Single card", "Back")]
        result = filter_q.find_similar_cards(cards)
        assert result == []

    def test_find_similar_cards_different(self):
        """Test similarity finding with different cards."""
        filter_q = QualityFilter()
        cards = [
            ("Python is a language", "Details"),
            ("JavaScript is different", "More details"),
        ]
        result = filter_q.find_similar_cards(cards, threshold=0.9)
        # Different cards should not be similar at high threshold
        assert len(result) == 0

    def test_filter_deck(self):
        """Test full deck filtering."""
        filter_q = QualityFilter()
        cards = [
            (
                "FastAPI is a {{c1::modern}} web framework.",
                "A Python framework",
            ),
            ("{{c1::Python}} is a programming language.", "Created by Guido"),
            ("The {{c1::the}} is a word.", "Article"),  # Trivial
        ]
        filtered, stats = filter_q.filter_deck(cards)
        assert isinstance(filtered, list)
        assert isinstance(stats, dict)
        assert "trivial_removed" in stats
        assert "similar_removed" in stats
        assert "kept" in stats


class TestTextSegment:
    """Test TextSegment dataclass."""

    def test_create_segment(self):
        """Test TextSegment creation."""
        segment = TextSegment(
            text="Sample text",
            start_page=1,
            end_page=2,
            token_count=100,
            is_sentence_boundary=True,
        )
        assert segment.text == "Sample text"
        assert segment.start_page == 1
        assert segment.end_page == 2
        assert segment.token_count == 100
        assert segment.is_sentence_boundary is True

    def test_create_segment_defaults(self):
        """Test TextSegment with defaults."""
        segment = TextSegment(
            text="Sample text",
            start_page=1,
            end_page=1,
            token_count=50,
        )
        assert segment.is_sentence_boundary is False


class TestTokenCounterEdgeCases:
    def test_token_counter_fallback(self):
        from unittest.mock import patch

        with patch(
            "builtins.__import__",
            side_effect=ImportError("No module named 'tiktoken'"),
        ):
            counter = TokenCounter()
            counter._available = False
            count = counter.count("Hello world")
            assert count == 3


class TestSemanticChunkerEdgeCases:
    """Test SemanticChunker edge cases."""

    def test_extract_text_from_pdf_error(self, tmp_path):
        """Test extract_text_from_pdf handles errors."""
        chunker = SemanticChunker()
        pdf_path = tmp_path / "nonexistent.pdf"
        result = chunker.extract_text_from_pdf(pdf_path)
        assert result == []

    def test_create_semantic_chunks_no_segments(self, tmp_path):
        """Test create_semantic_chunks with no segments."""
        chunker = SemanticChunker()
        pdf_path = tmp_path / "empty.pdf"
        # Create an empty file
        pdf_path.write_text("")
        chunks = list(chunker.create_semantic_chunks(pdf_path))
        assert chunks == []


class TestQualityFilterEdgeCases:
    """Test QualityFilter edge cases."""

    def test_find_similar_cards_exception(self):
        """Test find_similar_cards handles exception."""
        filter_q = QualityFilter()
        # Create cards that might cause exception
        cards = [
            ("", ""),  # Empty strings might cause issues
            ("", ""),
        ]
        # Should not raise, should return empty list
        result = filter_q.find_similar_cards(cards)
        assert isinstance(result, list)


class TestSemanticChunkerRegressions:
    @staticmethod
    def _word_count(text: str) -> int:
        return len(text.split())

    def _chunker_with_segments(
        self,
        monkeypatch,
        segments: list[TextSegment],
        *,
        target_tokens: int = 500,
        min_tokens: int = 200,
        max_tokens: int = 800,
        overlap_tokens: int = 50,
        boundaries: list[int] | None = None,
    ) -> SemanticChunker:
        chunker = SemanticChunker(
            target_tokens=target_tokens,
            min_tokens=min_tokens,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
        monkeypatch.setattr(chunker.token_counter, "count", self._word_count)
        monkeypatch.setattr(
            chunker, "extract_text_from_pdf", lambda _path: segments
        )
        monkeypatch.setattr(
            chunker,
            "find_semantic_boundaries",
            lambda _segments: [] if boundaries is None else boundaries,
        )
        return chunker

    def test_extract_text_skips_none_page_without_losing_later_page(
        self, monkeypatch, tmp_path
    ):
        class Page:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        reader = SimpleNamespace(pages=[Page(None), Page("retained page")])
        monkeypatch.setitem(
            sys.modules,
            "pypdf",
            SimpleNamespace(PdfReader=lambda *_args, **_kwargs: reader),
        )
        chunker = SemanticChunker()

        assert chunker.extract_text_from_pdf(tmp_path / "sample.pdf") == [
            TextSegment(
                "retained page",
                2,
                2,
                chunker.token_counter.count("retained page"),
            )
        ]

    def test_extract_text_rejects_oversized_pdf_before_reader(
        self, monkeypatch, tmp_path
    ):
        pdf_path = tmp_path / "oversized.pdf"
        pdf_path.write_bytes(b"012345")
        monkeypatch.setattr(
            SemanticChunker, "MAX_PDF_FILE_BYTES", 5, raising=False
        )

        def fail_if_reader_called(*_args, **_kwargs):
            pytest.fail("oversized PDF reached pypdf")

        monkeypatch.setitem(
            sys.modules,
            "pypdf",
            SimpleNamespace(PdfReader=fail_if_reader_called),
        )

        assert SemanticChunker().extract_text_from_pdf(pdf_path) == []

    def test_short_and_oversized_text_is_preserved_within_max_tokens(
        self, monkeypatch, tmp_path
    ):
        tokens = [f"sentinel_{index}" for index in range(22)]
        chunker = self._chunker_with_segments(
            monkeypatch,
            [TextSegment(" ".join(tokens), 1, 1, len(tokens))],
            min_tokens=200,
            max_tokens=10,
            overlap_tokens=0,
        )

        chunks = list(chunker.create_semantic_chunks(tmp_path / "sample.pdf"))

        assert [
            token for text, _, _ in chunks for token in text.split()
        ] == tokens
        assert all(
            chunker.token_counter.count(text) <= 10 for text, _, _ in chunks
        )

    def test_boundary_chunk_starts_at_first_represented_page(
        self, monkeypatch, tmp_path
    ):
        chunker = self._chunker_with_segments(
            monkeypatch,
            [
                TextSegment("one.", 1, 1, 1),
                TextSegment("two.", 2, 2, 1),
                TextSegment("three.", 3, 3, 1),
            ],
            target_tokens=2,
            min_tokens=1,
            max_tokens=10,
            boundaries=[1],
        )

        chunks = list(chunker.create_semantic_chunks(tmp_path / "sample.pdf"))

        assert [(start, end) for _, start, end in chunks] == [(1, 2), (3, 3)]

    def test_overlap_chunk_metadata_includes_the_overlapped_page(
        self, monkeypatch, tmp_path
    ):
        chunker = self._chunker_with_segments(
            monkeypatch,
            [
                TextSegment("Alpha. Beta. Gamma.", 1, 1, 3),
                TextSegment("Delta.", 2, 2, 1),
            ],
            min_tokens=1,
            max_tokens=3,
            overlap_tokens=1,
        )

        chunks = list(chunker.create_semantic_chunks(tmp_path / "sample.pdf"))

        assert chunks == [
            ("Alpha. Beta. Gamma.", 1, 1),
            ("Gamma. Delta.", 1, 2),
        ]
        assert all(
            chunker.token_counter.count(text) <= 3 for text, _, _ in chunks
        )


class TestQualityFilterRegressions:
    def test_stop_word_duplicate_is_detected_when_tfidf_has_no_vocabulary(
        self,
    ):
        cards = [
            ("could would should", "first detailed answer"),
            ("could would should", "second detailed answer"),
        ]

        assert QualityFilter().find_similar_cards(cards) == [(0, 1, 1.0)]

    def test_similarity_filter_does_not_materialize_a_dense_matrix(
        self, monkeypatch
    ):
        def fail_if_called(*_args, **_kwargs):
            pytest.fail("dense cosine similarity matrix must not be created")

        monkeypatch.setattr("scipy.sparse.csr_matrix.toarray", fail_if_called)
        cards = [
            ("alpha beta gamma", "first detailed answer"),
            ("alpha beta gamma", "second detailed answer"),
            ("delta epsilon zeta", "third detailed answer"),
        ]

        assert QualityFilter().find_similar_cards(cards) == [(0, 1, 1.0)]

    def test_similarity_filter_bounds_pair_memory_for_dense_inputs(
        self, monkeypatch
    ):
        filter_q = QualityFilter()
        monkeypatch.setattr(filter_q, "MAX_SIMILAR_PAIRS", 3)
        cards = [
            (f"alpha beta gamma {index}", "first detailed answer")
            for index in range(20)
        ]

        result = filter_q.find_similar_cards(cards)

        assert len(result) <= 3
