"""Primary application entry point."""

from __future__ import annotations

import sys

from flashcards_generator.interfaces.cli import main as cli_main
from flashcards_generator.interfaces.tui.app import FlashcardsApp


def main() -> None:
    """Launch the TUI without arguments and the CLI otherwise."""
    if len(sys.argv) > 1:
        cli_main()
        return
    FlashcardsApp().run()
