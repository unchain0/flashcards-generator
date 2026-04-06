"""Test script for semantic chunking on real books."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from flashcards_generator.infrastructure.logging_config import get_logger
from flashcards_generator.infrastructure.pdf_utils import PDFChunker
from flashcards_generator.infrastructure.semantic_chunker import (
    QualityFilter,
    SemanticChunker,
    TokenCounter,
)

logger = get_logger("test_semantic")


def test_token_counter():
    """Test token counting."""
    counter = TokenCounter()

    text = "FastAPI is a modern, fast (high-performance) web framework."
    count = counter.count(text)
    print(f"✓ Token count for sample text: {count} tokens")
    assert count > 0


def test_semantic_chunker(book_path: Path):
    """Test semantic chunking on a real book."""
    print(f"\n{'=' * 60}")
    print(f"Testing semantic chunking on: {book_path.name}")
    print(f"{'=' * 60}")

    chunker = SemanticChunker(
        target_tokens=500,
        min_tokens=200,
        max_tokens=800,
        overlap_tokens=50,
    )

    # Extract and chunk
    chunks = list(chunker.create_semantic_chunks(book_path))

    print(f"\nCreated {len(chunks)} semantic chunks:")
    total_tokens = 0
    for idx, (text, start_page, end_page) in enumerate(chunks[:5], 1):
        tokens = chunker.token_counter.count(text)
        total_tokens += tokens
        print(f"  Chunk {idx}: pages {start_page}-{end_page}, {tokens} tokens")
        if idx == 1:
            preview = text[:200].replace("\n", " ")
            print(f"    Preview: {preview}...")

    if len(chunks) > 5:
        print(f"  ... and {len(chunks) - 5} more chunks")

    print(f"\nTotal tokens processed: ~{total_tokens}")
    return chunks


def test_quality_filter():
    """Test quality filtering."""
    print(f"\n{'=' * 60}")
    print("Testing quality filter")
    print(f"{'=' * 60}")

    filter_q = QualityFilter()

    # Test trivial detection
    test_cards = [
        ("FastAPI is a {{c1::modern}} web framework.", "A Python framework"),
        ("{{c1::Python}} is a programming language.", "Created by Guido van Rossum"),
        ("This is a very good {{c1::framework}}.", "Yes"),  # Should be trivial
        ("The {{c1::the}} is a word.", "Article"),  # Should be trivial
    ]

    print("\nTrivial detection:")
    for front, back in test_cards:
        is_trivial = filter_q.is_trivial(front, back)
        status = "TRIVIAL" if is_trivial else "OK"
        print(f"  [{status}] {front[:50]}...")

    # Test similarity detection
    similar_cards = [
        ("FastAPI uses Pydantic for validation", "Details"),
        ("FastAPI uses Pydantic models", "More details"),
        ("Django is a Python framework", "Different"),
    ]

    print("\nSimilarity detection:")
    similar = filter_q.find_similar_cards(similar_cards)
    for i, j, sim in similar:
        print(f"  Cards {i} and {j} are similar ({sim:.2f})")

    # Test full filter
    _filtered, stats = filter_q.filter_deck(test_cards)
    print(f"\nFilter results: {stats}")


def compare_chunking_strategies(book_path: Path):
    """Compare old vs new chunking."""
    print(f"\n{'=' * 60}")
    print(f"Comparing chunking strategies: {book_path.name}")
    print(f"{'=' * 60}")

    # Old page-based chunking
    old_chunker = PDFChunker(chunk_size=50, overlap_pages=5)
    old_needs_chunking = old_chunker.needs_chunking(book_path)
    old_page_count = old_chunker.count_pages(book_path)

    print("\nOld strategy (page-based):")
    print(f"  Total pages: {old_page_count}")
    print(f"  Needs chunking: {old_needs_chunking}")

    if old_needs_chunking:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            chunks = list(old_chunker.chunk_pdf(book_path, Path(tmpdir)))
            print(f"  Chunks created: {len(chunks)}")

    # New token-based chunking
    new_chunker = SemanticChunker()
    chunks = list(new_chunker.create_semantic_chunks(book_path))

    print("\nNew strategy (token-based):")
    print(f"  Semantic chunks created: {len(chunks)}")

    token_counts = [new_chunker.token_counter.count(c[0]) for c in chunks]
    if token_counts:
        print(f"  Avg tokens per chunk: {sum(token_counts) / len(token_counts):.0f}")
        print(f"  Min tokens: {min(token_counts)}")
        print(f"  Max tokens: {max(token_counts)}")


def main():
    books_dir = Path(__file__).parent.parent / "input" / "Books"

    if not books_dir.exists():
        print(f"Books directory not found: {books_dir}")
        return

    books = list(books_dir.glob("*.pdf"))
    if not books:
        print("No PDF books found")
        return

    print(f"Found {len(books)} books to test")

    # Test token counter
    test_token_counter()

    # Test quality filter
    test_quality_filter()

    # Test with first book (smallest for speed)
    smallest_book = min(books, key=lambda p: p.stat().st_size)
    print(f"\nTesting with smallest book: {smallest_book.name}")

    test_semantic_chunker(smallest_book)

    # Compare strategies
    compare_chunking_strategies(smallest_book)

    print(f"\n{'=' * 60}")
    print("All tests completed successfully!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
