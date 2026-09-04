from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from flashcards_generator.domain.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
    Flashcard,
)
from flashcards_generator.infrastructure.chunk_state_repository import (
    FileSystemChunkStateRepository,
)


@pytest.fixture
def repository() -> FileSystemChunkStateRepository:
    return FileSystemChunkStateRepository()


@pytest.fixture
def sample_manifest() -> ChunkResumeManifest:
    now = datetime.now(timezone.utc)
    return ChunkResumeManifest(
        source_pdf="/tmp/source.pdf",
        source_signature="abc123",
        deck_name="Sample Deck",
        total_chunks=2,
        chunks=[
            ChunkState(
                chunk_index=0,
                status=ChunkStatus.COMPLETED,
                page_start=1,
                page_end=10,
                card_count=3,
                result_path="chunk-0.json",
                updated_at=now,
            ),
            ChunkState(
                chunk_index=1,
                status=ChunkStatus.PENDING,
                page_start=11,
                page_end=20,
                updated_at=now,
            ),
        ],
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sample_deck() -> Deck:
    now = datetime.now(timezone.utc)
    return Deck(
        name="Chunk Deck",
        description="Saved chunk output",
        flashcards=[
            Flashcard(front="Front 1", back="Back 1", tags=["chunk"]),
            Flashcard(front="Front 2", back="Back 2"),
        ],
        created_at=now,
    )


class TestFileSystemChunkStateRepository:
    def test_save_load_manifest_roundtrip(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
    ) -> None:
        state_path = tmp_path / "state" / "manifest.json"

        repository.save_manifest(state_path, sample_manifest)
        loaded_manifest = repository.load_manifest(state_path)

        assert loaded_manifest == sample_manifest

    def test_save_load_chunk_deck_roundtrip(
        self,
        repository: FileSystemChunkStateRepository,
        sample_deck: Deck,
        tmp_path,
    ) -> None:
        chunk_path = tmp_path / "results" / "chunk-0.json"

        repository.save_chunk_result(chunk_path, sample_deck)
        loaded_deck = repository.load_chunk_result(chunk_path)

        assert loaded_deck == sample_deck

    def test_atomic_overwrite_keeps_original_on_replace_failure(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_path = tmp_path / "manifest.json"
        state_path.write_text("original-content")

        original_replace = os.replace

        def failing_replace(source, target, **kwargs):
            if str(source).endswith(".tmp"):
                raise OSError("replace failed")
            return original_replace(source, target, **kwargs)

        monkeypatch.setattr(os, "replace", failing_replace)

        with pytest.raises(OSError, match="replace failed"):
            repository.save_manifest(state_path, sample_manifest)

        assert state_path.read_text() == "original-content"
        assert not list(state_path.parent.glob(".manifest.json.*.tmp"))

    def test_missing_manifest_returns_none(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        missing_path = tmp_path / "missing.json"

        assert repository.load_manifest(missing_path) is None

    def test_delete_operations(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        sample_deck: Deck,
        tmp_path,
    ) -> None:
        manifest_path = tmp_path / "state" / "manifest.json"
        results_dir = tmp_path / "results"
        chunk_path = results_dir / "chunk-0.json"

        repository.save_manifest(manifest_path, sample_manifest)
        repository.save_chunk_result(chunk_path, sample_deck)

        repository.delete_manifest(manifest_path)
        repository.delete_chunk_results(results_dir)

        assert not manifest_path.exists()
        assert not results_dir.exists()

    @pytest.mark.parametrize(
        ("write_path", "loader_name"),
        [
            ("manifest.json", "load_manifest"),
            ("chunk.json", "load_chunk_result"),
        ],
    )
    def test_corrupt_json_raises_validation_error(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
        write_path: str,
        loader_name: str,
    ) -> None:
        path = tmp_path / write_path
        path.write_text("{invalid json")

        loader = getattr(repository, loader_name)

        with pytest.raises(ValidationError):
            loader(path)

    def test_symlinked_temp_does_not_modify_victim(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
    ) -> None:
        state_path = tmp_path / "state.json"
        victim = tmp_path / "victim"
        victim.write_text("KEEP")
        state_path.with_name("state.json.tmp").symlink_to(victim)

        repository.save_manifest(state_path, sample_manifest)

        assert victim.read_text() == "KEEP"
        assert state_path.exists()

    def test_state_files_are_private_and_durable(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state_path = tmp_path / "resume" / "state.json"
        fsync_calls: list[int] = []
        monkeypatch.setattr(os, "fsync", lambda fd: fsync_calls.append(fd))

        repository.save_manifest(state_path, sample_manifest)

        assert fsync_calls
        assert state_path.stat().st_mode & 0o777 == 0o600
        assert state_path.parent.stat().st_mode & 0o777 == 0o700

    def test_resume_lock_rejects_concurrent_owner(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        resume_dir = tmp_path / "resume"

        with repository.resume_lock(resume_dir) as first_owner:
            assert first_owner is True
            with FileSystemChunkStateRepository().resume_lock(
                resume_dir
            ) as second_owner:
                assert second_owner is False
