from __future__ import annotations

from pathlib import Path
from typing import Final, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from flashcards_generator.services.local_generation_state import (
    GenerationJobSnapshot,
)

MAX_JOB_FILES: Final = 10
MAX_JOB_UPLOAD_BYTES: Final = 1024 * 1024 * 1024
MAX_JOB_REQUEST_BYTES: Final = MAX_JOB_UPLOAD_BYTES + 1024 * 1024
MAX_JOB_PARTS: Final = MAX_JOB_FILES + 5
UPLOAD_CHUNK_BYTES: Final = 1024 * 1024


class CompanionArtifactRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    url: str


class CompanionJobRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    message: str
    filenames: list[str]
    discovered_sources: int
    completed_sources: int
    skipped_sources: int
    failed_sources: int
    artifacts: list[CompanionArtifactRead]
    error: str | None


def companion_job_read(snapshot: GenerationJobSnapshot) -> CompanionJobRead:
    artifacts = [
        CompanionArtifactRead(
            name=Path(name).name,
            url=f"/v1/jobs/{snapshot.id}/artifacts/{quote(name, safe='/')}",
        )
        for name in snapshot.artifacts
    ]
    return CompanionJobRead(
        id=snapshot.id,
        status=snapshot.status,
        message=snapshot.message,
        filenames=list(snapshot.filenames),
        discovered_sources=snapshot.discovered_sources,
        completed_sources=snapshot.completed_sources,
        skipped_sources=snapshot.skipped_sources,
        failed_sources=snapshot.failed_sources,
        artifacts=artifacts,
        error=snapshot.error,
    )


__all__ = [
    "MAX_JOB_FILES",
    "MAX_JOB_PARTS",
    "MAX_JOB_REQUEST_BYTES",
    "MAX_JOB_UPLOAD_BYTES",
    "UPLOAD_CHUNK_BYTES",
    "CompanionArtifactRead",
    "CompanionJobRead",
    "companion_job_read",
]
