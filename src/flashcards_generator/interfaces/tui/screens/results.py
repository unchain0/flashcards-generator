"""Generated card and CSV result presentation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static

from flashcards_generator.domain.entities import Deck
from flashcards_generator.infrastructure.desktop_actions import (
    copy_text,
    open_path,
)

ResultsAction = Callable[[], None]


class ResultsPanel(Vertical):
    """Present generated cards and provide safe result actions."""

    def __init__(
        self,
        *,
        on_merge: ResultsAction | None = None,
        on_new_generation: ResultsAction | None = None,
        copy_action: Callable[[str], bool] = copy_text,
        open_action: Callable[[Path], bool] = open_path,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="workflow-panel")
        self._csv_path: Path | None = None
        self._on_merge = on_merge
        self._on_new_generation = on_new_generation
        self._copy_action = copy_action
        self._open_action = open_action

    def compose(self) -> ComposeResult:
        yield Label("Results", classes="workflow-title")
        yield Static("No completed run", id="results-summary")
        yield Static("No generated cards yet", id="results-cards")
        yield Static("No CSV output yet", id="results-csv")
        yield Static("No preview yet", id="results-preview")
        with Horizontal(classes="action-row"):
            yield Button("Copy CSV", id="results-copy")
            yield Button("Open CSV", id="results-open")
            yield Button("Merge", id="results-merge")
            yield Button("New generation", id="results-new")
        yield Static("", id="results-action-status")

    def show_decks(self, decks: Iterable[Deck]) -> None:
        """Render cards without changing the underlying domain models."""
        materialized = tuple(decks)
        lines = self._deck_lines(materialized)
        preview = self._card_preview(materialized)
        self.query_one("#results-cards", Static).update(
            "\n".join(lines) if lines else "No generated cards"
        )
        total_cards = sum(deck.total_cards for deck in materialized)
        self.query_one("#results-summary", Static).update(
            f"{len(materialized)} deck(s), {total_cards} card(s)"
        )
        self.query_one("#results-preview", Static).update(
            "\n".join(preview) if preview else "No card preview"
        )

    @staticmethod
    def _deck_lines(decks: tuple[Deck, ...]) -> list[str]:
        return [f"{deck.name}: {deck.total_cards} card(s)" for deck in decks]

    @staticmethod
    def _card_preview(decks: tuple[Deck, ...]) -> list[str]:
        return [
            f"{card.front} -> {card.back}"
            for deck in decks
            for card in deck.flashcards[:5]
        ]

    def show_csv(
        self,
        output_path: Path,
        rows_written: int,
        *,
        rows_before: int | None = None,
        duplicates_removed: int | None = None,
    ) -> None:
        """Render the service-provided output path and row counts."""
        self._csv_path = output_path
        summary = f"{output_path.name} - {rows_written} row(s)"
        if rows_before is not None and duplicates_removed is not None:
            summary += (
                f" ({rows_before} before, "
                f"{duplicates_removed} duplicate(s) removed)"
            )
        self.query_one("#results-csv", Static).update(summary)

    def show_csv_paths(self, paths: Iterable[Path]) -> None:
        """Render CSV paths returned by a completed generation."""
        values = tuple(paths)
        if values:
            self._csv_path = values[0]
            self.query_one("#results-csv", Static).update(
                "\n".join(str(path) for path in values)
            )

    def show_generation_summary(
        self,
        *,
        discovered: int,
        completed: int,
        skipped: int,
        failed: int,
        elapsed_seconds: float,
    ) -> None:
        """Render machine-consumable source counts and elapsed time."""
        self.query_one("#results-summary", Static).update(
            f"{completed}/{discovered} source(s), {skipped} skipped, "
            f"{failed} failed, {elapsed_seconds:.1f}s"
        )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle result actions without embedding application workflows."""
        button_id = event.button.id
        if self._handle_navigation_action(button_id):
            return
        if button_id == "results-copy":
            self._copy_csv()
        elif button_id == "results-open":
            self._open_csv()

    def _handle_navigation_action(self, button_id: str | None) -> bool:
        actions = {
            "results-merge": self._on_merge,
            "results-new": self._on_new_generation,
        }
        if button_id not in actions:
            return False
        action = actions[button_id]
        if action is not None:
            self.call_after_refresh(action)
        return True

    def _copy_csv(self) -> None:
        if self._csv_path is None:
            self._set_action_status("No CSV available")
            return
        try:
            copied = self._copy_action(
                self._csv_path.read_text(encoding="utf-8")
            )
        except OSError:
            self._set_action_status("Unable to copy CSV")
            return
        if copied:
            self._set_action_status("CSV copied")
        else:
            self._set_action_status("Unable to copy CSV")

    def _open_csv(self) -> None:
        if self._csv_path is None:
            self._set_action_status("No CSV available")
            return
        try:
            opened = self._open_action(self._csv_path)
        except OSError:
            opened = False
        if opened:
            self._set_action_status("Open command sent")
        else:
            self._set_action_status("Unable to open CSV")

    def _set_action_status(self, message: str) -> None:
        self.query_one("#results-action-status", Static).update(message)
