from __future__ import annotations

import logging
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.engines.quality import QualityFilter
from flashcards_generator.services.generation_models import _ChunkRun

logger = logging.getLogger("use_cases")


class QualityContext(Protocol):
    def _remove_trivial_cards(
        self, deck: Deck, quality_filter: QualityFilter
    ) -> tuple[list[Flashcard], int]: ...

    def _remove_similar_cards(
        self, cards_to_keep: list[Flashcard], quality_filter: QualityFilter
    ) -> tuple[list[Flashcard], int]: ...

    def _apply_quality_filter(self, deck: Deck) -> None: ...


def combine_chunk_decks(
    context: QualityContext, run: _ChunkRun
) -> Deck | None:
    """Combine completed chunk decks and apply final deduplication."""
    all_flashcards = [
        card
        for chunk_index in range(1, len(run.chunks) + 1)
        for card in run.chunk_decks[chunk_index].flashcards
    ]
    if not all_flashcards:
        logger.error("No flashcards generated from any chunk")
        return None

    logger.info(f"Total flashcards from all chunks: {len(all_flashcards)}")
    combined_deck = Deck(
        name=run.deck_name,
        description=f"Deck de {run.deck_name} ({len(run.chunks)} chunks)",
        flashcards=all_flashcards,
        notebook_id="",
    )
    removed = combined_deck.deduplicate(similarity_threshold=0.85)
    if removed > 0:
        logger.info(f"Removed {removed} duplicate flashcards")
    context._apply_quality_filter(combined_deck)
    return combined_deck


def apply_quality_filter(context: QualityContext, deck: Deck) -> None:
    """Apply quality filtering to remove trivial and similar cards."""
    if not deck.flashcards:
        return

    quality_filter = QualityFilter()
    cards_to_keep, trivial_count = context._remove_trivial_cards(
        deck, quality_filter
    )
    if trivial_count > 0:
        logger.info(f"Quality filter removed {trivial_count} trivial cards")

    cards_to_keep, removed_similar = context._remove_similar_cards(
        cards_to_keep, quality_filter
    )
    if removed_similar > 0:
        logger.info(f"Quality filter removed {removed_similar} similar cards")
    deck.flashcards = cards_to_keep


def remove_trivial_cards(
    deck: Deck, quality_filter: QualityFilter
) -> tuple[list[Flashcard], int]:
    """Return cards that have enough meaningful content."""
    cards_to_keep = []
    trivial_count = 0
    for card in deck.flashcards:
        if quality_filter.is_trivial(card.front, card.back):
            trivial_count += 1
        else:
            cards_to_keep.append(card)
    return cards_to_keep, trivial_count


def remove_similar_cards(
    cards_to_keep: list[Flashcard], quality_filter: QualityFilter
) -> tuple[list[Flashcard], int]:
    """Remove the later card from each similar pair."""
    if len(cards_to_keep) < 2:
        return cards_to_keep, 0

    indices_to_remove = similar_card_removal_indices(
        cards_to_keep, quality_filter
    )
    filtered_cards = [
        card
        for index, card in enumerate(cards_to_keep)
        if index not in indices_to_remove
    ]
    return filtered_cards, len(cards_to_keep) - len(filtered_cards)


def similar_card_removal_indices(
    cards_to_keep: list[Flashcard], quality_filter: QualityFilter
) -> set[int]:
    card_tuples = [(card.front, card.back) for card in cards_to_keep]
    return {
        second_index
        for _, second_index, _ in quality_filter.find_similar_cards(
            card_tuples
        )
    }
