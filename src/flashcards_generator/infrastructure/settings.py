"""Typed, platform-neutral persistence for application settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from platformdirs import user_config_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Settings(BaseModel):
    """Persisted defaults used by flashcard generation."""

    model_config = ConfigDict(extra="forbid")

    input_dir: Path = Path(".")
    output_dir: Path = Path("output")
    language: str = "pt_BR"
    difficulty: str = "medium"
    quantity: str = "standard"
    instructions: str = ""
    timeout: int = Field(default=900, gt=0)
    resume: bool = True
    include_pattern: str | None = None
    exclude_pattern: str | None = None


class SettingsRepository:
    """Persist settings as UTF-8 JSON in the user's platform config folder."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (
            Path(user_config_path("flashcards-generator")) / "settings.json"
        )

    def save(self, settings: Settings) -> None:
        """Atomically save settings, creating the config directory if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            settings.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
                fd = -1
                file_obj.write(payload)
                file_obj.write("\n")
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temporary, self.path)
        finally:
            if fd != -1:
                os.close(fd)
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass

    def load(self) -> Settings:
        """Load settings, deterministically returning defaults on bad input."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return Settings.model_validate(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValidationError):
            return Settings()


FileSystemSettingsRepository = SettingsRepository
