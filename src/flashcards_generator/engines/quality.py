from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import ClassVar, Protocol

from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)


class SimilarityCoordinates(Protocol):
    col: Iterable[int]
    data: Iterable[float]


class SimilarityMatrix(Protocol):
    @property
    def T(self) -> SimilarityMatrix: ...

    def getrow(self, index: int) -> SimilarityMatrix: ...

    def __getitem__(self, index: slice) -> SimilarityMatrix: ...

    def __matmul__(self, other: SimilarityMatrix) -> SimilarityProduct: ...


class SimilarityProduct(Protocol):
    def tocoo(self) -> SimilarityCoordinates: ...


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

    SUBJECTIVE_WORDS: ClassVar[set[str]] = {
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

    MIN_CONTENT_WORDS = 3
    MAX_SIMILARITY = 0.85
    MAX_SIMILAR_PAIRS = 100_000

    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(
            max_features=50, stop_words="english"
        )

    def is_trivial(self, front: str, back: str) -> bool:
        """Check if flashcard is too trivial."""
        return (
            self._has_insufficient_content(front)
            or self._has_short_back(back)
            or self._has_subjective_language(front)
        )

    def _has_insufficient_content(self, front: str) -> bool:
        words = front.lower().split()
        content_words = [
            word for word in words if word not in self.TRIVIAL_WORDS
        ]
        return len(content_words) < self.MIN_CONTENT_WORDS

    @staticmethod
    def _has_short_back(back: str) -> bool:
        return len(back.split()) < 2

    @classmethod
    def _has_subjective_language(cls, front: str) -> bool:
        return any(word in front.lower() for word in cls.SUBJECTIVE_WORDS)

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
        non_trivial, trivial_removed = self._partition_trivial_cards(cards)
        filtered, similar_removed = self._remove_similar_cards(non_trivial)
        stats = {
            "trivial_removed": trivial_removed,
            "similar_removed": similar_removed,
            "kept": len(filtered),
        }
        logger.info(
            f"Quality filter: removed {trivial_removed} trivial, "
            f"{similar_removed} similar, kept {len(filtered)}"
        )
        return filtered, stats

    def _partition_trivial_cards(
        self, cards: list[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]], int]:
        non_trivial = []
        trivial_removed = 0
        for front, back in cards:
            if self.is_trivial(front, back):
                trivial_removed += 1
            else:
                non_trivial.append((front, back))
        return non_trivial, trivial_removed

    def _remove_similar_cards(
        self, cards: list[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]], int]:
        to_remove = {j for _i, j, _score in self.find_similar_cards(cards)}
        filtered = [
            card for index, card in enumerate(cards) if index not in to_remove
        ]
        return filtered, len(to_remove)
