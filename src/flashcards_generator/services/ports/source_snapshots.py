from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SourceSnapshotsPort(Protocol):
    def compute_signature(self, source_path: Path) -> str: ...

    def create(self, source_path: Path, output_path: Path) -> Path: ...

    def cleanup(self, snapshot_path: Path) -> None: ...
