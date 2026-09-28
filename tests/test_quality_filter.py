from types import SimpleNamespace

import pytest
from scipy.sparse import csr_matrix

from flashcards_generator.engines import quality as quality_filter_module
from flashcards_generator.engines.quality import QualityFilter


def test_init() -> None:
    quality_filter = QualityFilter()
    assert hasattr(quality_filter, "vectorizer")
    assert hasattr(quality_filter, "TRIVIAL_WORDS")
    assert len(quality_filter.TRIVIAL_WORDS) > 0


def test_is_trivial_valid_card() -> None:
    front = "FastAPI is a {{c1::modern}} web framework."
    assert not QualityFilter().is_trivial(front, "A Python framework")


def test_is_trivial_only_stopwords() -> None:
    front = "The {{c1::the}} is a word."
    assert QualityFilter().is_trivial(front, "Article")


def test_is_trivial_short_back() -> None:
    front = "Python is a {{c1::language}}."
    assert QualityFilter().is_trivial(front, "Yes")


def test_is_trivial_subjective() -> None:
    front = "This is a very {{c1::good}} solution."
    assert QualityFilter().is_trivial(front, "Positive")


def test_find_similar_cards_empty() -> None:
    assert QualityFilter().find_similar_cards([]) == []


def test_find_similar_cards_single() -> None:
    assert QualityFilter().find_similar_cards([("Single card", "Back")]) == []


def test_find_similar_cards_different() -> None:
    cards = [
        ("Python is a language", "Detailed answer"),
        ("Quantum systems correlate", "Another detailed answer"),
    ]
    assert QualityFilter().find_similar_cards(cards, threshold=0.9) == []


def test_filter_deck_preserves_order_counts_removals_and_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quality_filter = QualityFilter()
    cards = [
        ("FastAPI is a {{c1::modern}} web framework.", "A Python framework"),
        (
            "  fastapi is a {{c1::modern}} web framework. ",
            "Another comprehensive answer",
        ),
        (
            "FastAPI is a {{c1::modern}} web framework.",
            "Third comprehensive answer",
        ),
        (
            "Quantum entanglement correlates distant particle states.",
            "Correlated measurement outcomes",
        ),
        ("The {{c1::the}} is a word.", "Article"),
    ]
    messages: list[str] = []
    monkeypatch.setattr(
        quality_filter_module,
        "logger",
        SimpleNamespace(info=messages.append),
    )

    filtered, stats = quality_filter.filter_deck(cards)

    assert filtered == [cards[0], cards[3]]
    assert stats == {
        "trivial_removed": 1,
        "similar_removed": 2,
        "kept": 2,
    }
    assert messages == ["Quality filter: removed 1 trivial, 2 similar, kept 2"]


def test_filter_deck_empty_preserves_zero_statistics() -> None:
    filtered, stats = QualityFilter().filter_deck([])
    assert filtered == []
    assert stats == {"trivial_removed": 0, "similar_removed": 0, "kept": 0}


def test_find_similar_cards_handles_vectorizer_error() -> None:
    cards = [("", ""), ("", "")]
    assert isinstance(QualityFilter().find_similar_cards(cards), list)


def test_find_similar_cards_logs_unexpected_vectorizer_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quality_filter = QualityFilter()
    messages: list[str] = []

    def fail_fit_transform(_fronts: list[str]) -> None:
        raise RuntimeError("vectorizer unavailable")

    monkeypatch.setattr(
        quality_filter.vectorizer, "fit_transform", fail_fit_transform
    )
    monkeypatch.setattr(
        quality_filter_module,
        "logger",
        SimpleNamespace(warning=messages.append),
    )

    assert (
        quality_filter.find_similar_cards([
            ("first distinct front", "Detailed answer"),
            ("second front", "More details"),
        ])
        == []
    )
    assert messages == ["Similarity analysis failed: vectorizer unavailable"]


def test_stop_word_duplicate_is_detected_when_tfidf_has_no_vocabulary() -> (
    None
):
    cards = [
        ("could would should", "first detailed answer"),
        ("could would should", "second detailed answer"),
    ]
    assert QualityFilter().find_similar_cards(cards) == [(0, 1, 1.0)]


def test_similarity_filter_does_not_materialize_a_dense_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "dense cosine similarity matrix must not be created"
        )

    monkeypatch.setattr(csr_matrix, "toarray", fail_if_called)
    cards = [
        ("alpha beta gamma", "first detailed answer"),
        ("alpha beta gamma", "second detailed answer"),
        ("delta epsilon zeta", "third detailed answer"),
    ]
    assert QualityFilter().find_similar_cards(cards) == [(0, 1, 1.0)]


def test_similarity_filter_bounds_pair_memory_for_dense_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quality_filter = QualityFilter()
    monkeypatch.setattr(quality_filter, "MAX_SIMILAR_PAIRS", 3)
    cards = [
        (f"alpha beta gamma {index}", "first detailed answer")
        for index in range(20)
    ]
    assert len(quality_filter.find_similar_cards(cards)) <= 3
