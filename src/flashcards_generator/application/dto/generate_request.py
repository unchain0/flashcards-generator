"""DTO for generate flashcards use case."""

from pathlib import Path

from pydantic import BaseModel, Field

_ = Path  # Explicit runtime usage for pydantic model validation


class GenerateFlashcardsRequest(BaseModel):
    """Request to generate flashcards."""

    model_config = {"arbitrary_types_allowed": True}

    input_dir: Path
    output_dir: Path
    difficulty: str = Field(default="medium")
    quantity: str = Field(default="standard")
    instructions: str = Field(default="")
    wait_for_completion: bool = Field(default=True)
    timeout: int = Field(default=900)
