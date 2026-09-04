"""Adapters implementing domain ports."""

from flashcards_generator.adapters.anki_connect_adapter import (
    AnkiConnectAdapter,
)
from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter

__all__ = ["AnkiConnectAdapter", "NotebookLMAdapter"]
