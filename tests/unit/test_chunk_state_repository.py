from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from flashcards_generator.domain_models.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
    Flashcard,
)
from flashcards_generator.integrations.chunk_state_repository import (
    FileSystemChunkStateRepository,
    suppress_os_error,
)


@pytest.fixture
def repository() -> FileSystemChunkStateRepository:
    return FileSystemChunkStateRepository()


@pytest.fixture
def sample_manifest() -> ChunkResumeManifest:
    now = datetime.now(UTC)
    return ChunkResumeManifest(
        source_pdf="/tmp/source.pdf",
        source_signature="abc123",
        deck_name="Sample Deck",
        total_chunks=2,
        single_cloze=False,
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
    now = datetime.now(UTC)
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
    def test_delete_missing_manifest_is_idempotent(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        repository.delete_manifest(tmp_path / "missing.json")

    def test_delete_chunk_results_removes_only_a_symlink(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        results = tmp_path / "results"
        results.symlink_to(target, target_is_directory=True)

        repository.delete_chunk_results(results)

        assert not results.exists()
        assert target.is_dir()

    def test_read_rejects_a_directory_and_closes_its_descriptor(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        with pytest.raises(OSError, match="not a regular file"):
            repository.load_chunk_result(tmp_path)

    def test_atomic_write_closes_temporary_descriptor_on_fchmod_failure(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_fchmod(_descriptor: int, _mode: int) -> None:
            raise OSError("fchmod failed")

        monkeypatch.setattr(
            "flashcards_generator.integrations.chunk_state_repository.os.fchmod",
            fail_fchmod,
        )

        with pytest.raises(OSError, match="fchmod failed"):
            repository.save_manifest(
                tmp_path / "manifest.json", sample_manifest
            )

        assert list(tmp_path.glob(".manifest.json.*.tmp")) == []

    def test_read_rejects_symlink_parent_directories(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        linked_parent = tmp_path / "linked"
        linked_parent.symlink_to(target, target_is_directory=True)

        with pytest.raises(OSError, match="not a real directory"):
            repository.load_chunk_result(linked_parent / "result.json")

    def test_suppress_os_error_handles_cleanup_failure(self) -> None:
        with suppress_os_error():
            raise OSError("cleanup failed")

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

    def test_load_manifest_rejects_legacy_shape_without_single_cloze(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
    ) -> None:
        state_path = tmp_path / "manifest.json"
        payload = json.loads(sample_manifest.model_dump_json())
        del payload["single_cloze"]
        state_path.write_text(json.dumps(payload))

        with pytest.raises(ValidationError):
            repository.load_manifest(state_path)

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

    def test_symlinked_parent_directory_is_rejected(
        self,
        repository: FileSystemChunkStateRepository,
        sample_manifest: ChunkResumeManifest,
        tmp_path,
    ) -> None:
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        symlink_dir = tmp_path / "linked"
        symlink_dir.symlink_to(target_dir, target_is_directory=True)
        state_path = symlink_dir / "nested" / "manifest.json"

        with pytest.raises(
            OSError, match="State directory is not a real directory"
        ):
            repository.save_manifest(state_path, sample_manifest)

        assert not (target_dir / "nested").exists()

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

    def test_resume_lock_rejects_a_symlinked_lock_file(
        self,
        repository: FileSystemChunkStateRepository,
        tmp_path,
    ) -> None:
        resume_dir = tmp_path / "resume"
        target = tmp_path / "target"
        target.touch()
        (tmp_path / ".resume.lock").symlink_to(target)

        with pytest.raises(OSError), repository.resume_lock(resume_dir):
            pytest.fail("symlinked lock file was opened")

        assert target.read_bytes() == b""
