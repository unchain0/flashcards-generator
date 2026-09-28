"""DTO for CSV merge use case."""

from pathlib import Path, PurePath

from pydantic import BaseModel, Field, field_validator


class MergeCsvRequest(BaseModel):
    """Request to merge CSV flashcard files."""

    model_config = {"arbitrary_types_allowed": True}

    folder_path: Path
    output_filename: str = Field(default="merged_flashcards.csv")
    deduplicate: bool = Field(default=False)

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, value: str) -> str:
        path = PurePath(value)
        if not value or path.is_absolute() or path.name != value:
            raise ValueError(
                "output_filename must be a nonempty relative basename"
            )
        return value

    recursive: bool = Field(default=True)
