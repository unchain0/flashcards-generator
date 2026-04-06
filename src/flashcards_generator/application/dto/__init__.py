"""Data Transfer Objects for use cases."""

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.dto.merge_request import MergeCsvRequest

__all__ = ["GenerateFlashcardsRequest", "MergeCsvRequest"]
