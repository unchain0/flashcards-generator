"""Tests for semantic chunking functionality."""

from unittest.mock import patch

from scipy.sparse import csr_matrix

from flashcards_generator.integrations import (
    semantic_analysis as semantic_analysis_module,
)
from flashcards_generator.integrations.semantic_chunker import (
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
                "flashcards_generator.integrations.semantic_analysis."
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

    def test_get_overlap_text(self, monkeypatch):
        """Test overlap text extraction."""
        chunker = SemanticChunker(overlap_tokens=2)
        monkeypatch.setattr(
            chunker.token_counter, "count", lambda text: len(text.split())
        )
        previous = ["first two", "middle two", "latest two"]
        overlap = chunker._get_overlap_text(previous)
        assert overlap == ["latest two"]


def test_adjacent_similarity_scores_for_single_segment_are_empty() -> None:
    assert (
        semantic_analysis_module.SemanticAnalysis._adjacent_similarity_scores(
            object(), 1
        )
        == []
    )


def test_split_to_max_tokens_splits_long_word_without_encoding(
    monkeypatch,
) -> None:
    chunker = SemanticChunker(max_tokens=2)
    monkeypatch.setattr(chunker.token_counter, "count", lambda text: len(text))
    monkeypatch.setattr(chunker.token_counter, "encoding", None)

    assert chunker._split_to_max_tokens("ok abcde hi") == [
        "ok",
        "ab",
        "cd",
        "e",
        "hi",
    ]


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


def test_split_long_word_uses_token_encoding(monkeypatch):
    class Encoding:
        def encode(self, _text: str) -> list[int]:
            return [0, 1, 2, 3, 4]

        def decode(self, token_ids: list[int]) -> str:
            return "".join(str(token_id) for token_id in token_ids)

    chunker = SemanticChunker(max_tokens=2)
    monkeypatch.setattr(chunker.token_counter, "encoding", Encoding())

    assert chunker._split_long_word("longword") == ["01", "23", "4"]
