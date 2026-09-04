"""Composable workflow panels for the primary Textual shell."""

from flashcards_generator.interfaces.tui.screens.generate import (
    GeneratePanel,
    ProgressPanel,
)
from flashcards_generator.interfaces.tui.screens.merge import MergePanel
from flashcards_generator.interfaces.tui.screens.notebooklm import (
    CleanupConfirmation,
    NotebookLMPanel,
)
from flashcards_generator.interfaces.tui.screens.results import ResultsPanel
from flashcards_generator.interfaces.tui.screens.settings import SettingsPanel

__all__ = [
    "CleanupConfirmation",
    "GeneratePanel",
    "MergePanel",
    "NotebookLMPanel",
    "ProgressPanel",
    "ResultsPanel",
    "SettingsPanel",
]
