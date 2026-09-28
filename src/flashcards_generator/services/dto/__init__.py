"""Data Transfer Objects for use cases."""

from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.dto.merge_request import MergeCsvRequest

__all__ = ["GenerateFlashcardsRequest", "MergeCsvRequest"]
