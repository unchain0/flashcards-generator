"""Text inputs that leave application shortcuts available while focused."""

from __future__ import annotations

from textual.widgets import Input

_APPLICATION_SHORTCUTS = frozenset({
    "q",
    "g",
    "r",
    "m",
    "n",
    "s",
    "question_mark",
    "ctrl+r",
    "escape",
})


class ShortcutInput(Input):
    """An input that does not consume keys owned by the application shell."""

    def check_consume_key(
        self,
        key: str,
        character: str | None,
    ) -> bool:
        """Keep global navigation keys out of editable field contents."""
        if key in _APPLICATION_SHORTCUTS:
            return False
        return super().check_consume_key(key, character)
