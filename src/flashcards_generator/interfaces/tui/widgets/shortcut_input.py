"""Text inputs with focus-aware application shortcut routing."""

from __future__ import annotations

from textual.widgets import Input


class ShortcutInput(Input):
    """Use Textual's native printable-key consumption while focused."""
