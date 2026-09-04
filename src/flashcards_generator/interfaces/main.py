"""Primary Textual entry point."""

from __future__ import annotations

import sys

from flashcards_generator.interfaces.tui.app import FlashcardsApp


def main() -> None:
    """Launch the Textual app for every primary entrypoint invocation."""
    FlashcardsApp(show_help="--help" in sys.argv[1:]).run()
