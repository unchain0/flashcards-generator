"""Export decks to various formats."""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from flashcards_generator.engines.math import (
    convert_to_anki_math_format,
)

if TYPE_CHECKING:
    from flashcards_generator.domain_models.entities import Deck


class DeckExporter:
    """Export deck to various file formats."""

    @staticmethod
    def export_json(deck: Deck, path: Path) -> None:
        """Export deck to JSON file."""
        path.write_text(
            deck.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def export_csv(deck: Deck, path: Path) -> None:
        """Export deck to CSV file without header (2 columns: front, back)."""
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file_obj:
                temporary_path = Path(file_obj.name)
                writer = csv.writer(file_obj, quoting=csv.QUOTE_ALL)
                for card in deck.flashcards:
                    front = convert_to_anki_math_format(card.front)
                    back = convert_to_anki_math_format(card.back)
                    writer.writerow([front, back])
                file_obj.flush()
                os.fsync(file_obj.fileno())

            temporary_path.replace(path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                primary_error = sys.exception()
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    if primary_error is None:  # pragma: no cover
                        raise
                    primary_error.add_note(
                        "Temporary CSV cleanup also failed (OSError)"
                    )

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
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("\n".join(lines))
            writer = csv.writer(
                f,
                delimiter="\t",
                quoting=csv.QUOTE_ALL,
                lineterminator="\n",
            )
            for card in deck.flashcards:
                front = convert_to_anki_math_format(card.front)
                back = convert_to_anki_math_format(card.back)
                writer.writerow([front, back])

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
            lines.extend([
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
            ])
        path.write_text("\n".join(lines), encoding="utf-8")
