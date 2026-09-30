from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str = Field(
        default="pt_BR",
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    quantity: Literal["fewer", "standard", "more"] = "standard"
    timeout: int = Field(default=900, ge=30, le=7200)
    instructions: str = Field(default="", max_length=10000)
    single_cloze: bool = False
