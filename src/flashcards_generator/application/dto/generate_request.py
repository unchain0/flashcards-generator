"""Data Transfer Objects for use cases."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class GenerateFlashcardsRequest:
    """Request to generate flashcards."""

    input_dir: Path
    output_dir: Path
    difficulty: str = "medium"
    quantity: str = "standard"
    instructions: str = ""
    wait_for_completion: bool = True
    timeout: int = 900
