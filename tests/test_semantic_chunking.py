"""Tests for semantic chunking functionality."""

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
