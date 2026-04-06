"""Export decks to various formats."""

import csv
from typing import TYPE_CHECKING

from flashcards_generator.application.math_processor import convert_to_anki_math_format

if TYPE_CHECKING:
    from pathlib import Path

    from flashcards_generator.domain.entities import Deck


class DeckExporter:
    """Export deck to various file formats."""

    @staticmethod
    def export_json(deck: Deck, path: Path) -> None:
        """Export deck to JSON file."""
        path.write_text(
            deck.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def export_csv(deck: Deck, path: Path) -> None:
        """Export deck to CSV file without header (2 columns: front, back)."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            for card in deck.flashcards:
                front = convert_to_anki_math_format(card.front)
                back = convert_to_anki_math_format(card.back)
                writer.writerow([front, back])

    @staticmethod
    def export_anki(deck: Deck, path: Path) -> None:
        """Export deck to Anki TSV format (2 columns: front, back)."""
        lines = [
            f"# Deck: {deck.name}",
            f"# Gerado: {deck.created_at.isoformat()}",
            "",
            "#separator:tab",
            "#html:true",
            "",
        ]
        for card in deck.flashcards:
            front = convert_to_anki_math_format(card.front)
            back = convert_to_anki_math_format(card.back)
            lines.append(f"{front}\t{back}")
        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def export_markdown(deck: Deck, path: Path) -> None:
        """Export deck to Markdown file."""
        lines = [
            f"# {deck.name}",
            "",
            f"**Total:** {deck.total_cards} cards",
            "",
            "---",
            "",
        ]
        for i, card in enumerate(deck.flashcards, 1):
            lines.extend(
                [
                    f"## Card {i}",
                    "",
                    f"**Frente:** {card.front}",
                    "",
                    f"**Verso:** {card.back}",
                    "",
                    f"**Tags:** {', '.join(card.tags)}",
                    "",
                    "---",
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
