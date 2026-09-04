"""Textual application shell for the flashcards generator."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Label,
    Static,
    TabbedContent,
    TabPane,
)


class HelpScreen(ModalScreen[None]):
    """Show the keyboard shortcuts without leaving the current screen."""

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [("escape", "close_help", "Close")]

    def compose(self) -> ComposeResult:
        """Render the shortcuts panel."""
        with Container(id="help-dialog"):
            yield Label("Keyboard shortcuts", id="help-title")
            yield Static(
                "q  Quit\n"
                "g  Generate\n"
                "r  Results\n"
                "m  Merge\n"
                "n  NotebookLM\n"
                "s  Settings\n"
                "?  Help\n"
                "Esc  Close",
                id="help-shortcuts",
            )

    def action_close_help(self) -> None:
        """Close the help panel."""
        self.dismiss(None)


class FlashcardsApp(App[None]):
    """Primary Textual shell for all flashcard workflows."""

    TITLE = "Flashcards Generator"
    CSS_PATH = "styles.tcss"
    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        ("q", "quit", "Quit"),
        ("g", "show_generate", "Generate"),
        ("r", "show_results", "Results"),
        ("m", "show_merge", "Merge"),
        ("n", "show_notebooklm", "NotebookLM"),
        ("s", "show_settings", "Settings"),
        ("?", "show_help", "Help"),
    ]

    def compose(self) -> ComposeResult:
        """Render the navigation shell and workflow placeholders."""
        yield Header(show_clock=True)
        with TabbedContent(id="main-tabs"):
            with TabPane("Generate", id="generate"):
                yield from self._workflow_panel(
                    "Generate flashcards",
                    "Choose source files, configure generation, and start a run.",
                )
            with TabPane("Results", id="results"):
                yield from self._workflow_panel(
                    "Results",
                    "Completed runs and generated CSV files will appear here.",
                )
            with TabPane("Merge", id="merge"):
                yield from self._workflow_panel(
                    "Merge CSV files",
                    "Combine and deduplicate exported flashcards.",
                )
            with TabPane("NotebookLM", id="notebooklm"):
                yield from self._workflow_panel(
                    "NotebookLM",
                    "Check authentication and manage notebooks.",
                )
            with TabPane("Settings", id="settings"):
                yield from self._workflow_panel(
                    "Settings",
                    "Persist input, output, language, and generation defaults.",
                )
        yield Footer()

    @staticmethod
    def _workflow_panel(title: str, description: str) -> ComposeResult:
        """Build a consistent placeholder panel for each workflow."""
        with Vertical(classes="workflow-panel"):
            yield Label(title, classes="workflow-title")
            yield Static(description, classes="workflow-description")

    def action_show_generate(self) -> None:
        """Focus the Generate tab."""
        self._show_tab("generate")

    def action_show_results(self) -> None:
        """Focus the Results tab."""
        self._show_tab("results")

    def action_show_merge(self) -> None:
        """Focus the Merge tab."""
        self._show_tab("merge")

    def action_show_notebooklm(self) -> None:
        """Focus the NotebookLM tab."""
        self._show_tab("notebooklm")

    def action_show_settings(self) -> None:
        """Focus the Settings tab."""
        self._show_tab("settings")

    def action_show_help(self) -> None:
        """Open keyboard shortcut help."""
        self.push_screen(HelpScreen())

    def _show_tab(self, tab_id: str) -> None:
        """Activate a tab by its semantic identifier."""
        self.query_one("#main-tabs", TabbedContent).active = tab_id
