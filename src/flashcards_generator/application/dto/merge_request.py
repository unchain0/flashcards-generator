"""DTO for CSV merge use case."""

from pathlib import Path

from pydantic import BaseModel, Field

_ = Path  # Explicit runtime usage for pydantic model validation


class MergeCsvRequest(BaseModel):
    """Request to merge CSV flashcard files."""

    model_config = {"arbitrary_types_allowed": True}

    folder_path: Path
    output_filename: str = Field(default="merged_flashcards.csv")
    deduplicate: bool = Field(default=False)
    recursive: bool = Field(default=True)
