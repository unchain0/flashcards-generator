from pathlib import Path

from pydantic import BaseModel, Field


class Config(BaseModel):
    input_dir: Path
    output_dir: Path = Field(default=Path("./output"))
    difficulty: str = Field(default="medium")
    quantity: str = Field(default="standard")
    instructions: str = Field(default="")
    language: str = Field(default="pt_BR")
    wait_for_completion: bool = Field(default=True)
    timeout: int = Field(default=900)

    class Config:
        arbitrary_types_allowed = True


class SourceInfo(BaseModel):
    source_id: str
    file_path: Path
    status: str = Field(default="processing")
