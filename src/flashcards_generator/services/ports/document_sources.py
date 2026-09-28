from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DocumentSelection:
    explicit_files: tuple[str, ...] = ()
    include_pattern: str | None = None
    exclude_pattern: str | None = None


class DocumentSourcesPort(Protocol):
    def find_all_sources(
        self, input_path: Path, selection: DocumentSelection
    ) -> list[Path]: ...

    def is_safe_source(self, file_path: Path, input_path: Path) -> bool: ...

    def get_deck_name(self, source_path: Path, input_path: Path) -> str: ...

    def get_output_subdir(
        self, source_path: Path, input_path: Path, output_path: Path
    ) -> Path: ...
