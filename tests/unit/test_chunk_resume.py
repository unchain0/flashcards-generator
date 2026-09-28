from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from flashcards_generator.domain_models.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
    Flashcard,
)
from flashcards_generator.services import chunk_resume
from flashcards_generator.services.ports import ChunkStatePort


def _deck() -> Deck:
    return Deck(
        name="resume",
        flashcards=[Flashcard(front="Question", back="Answer")],
    )


def _state(
    chunk_index: int,
    result_path: Path,
    *,
    status: ChunkStatus = ChunkStatus.COMPLETED,
    card_count: int = 1,
) -> ChunkState:
    return ChunkState(
        chunk_index=chunk_index,
        status=status,
        card_count=card_count,
        result_path=str(result_path),
        updated_at=datetime.now(UTC),
    )


def _manifest(
    pdf_path: Path,
    resume_dir: Path,
    *,
    chunks: list[ChunkState] | None = None,
) -> ChunkResumeManifest:
    now = datetime.now(UTC)
    return ChunkResumeManifest(
        source_pdf=str(pdf_path),
        source_signature="sha256:source",
        deck_name="deck",
        total_chunks=2,
        chunks=chunks or [],
        created_at=now,
        updated_at=now,
    )


def _repository() -> MagicMock:
    return MagicMock(spec=ChunkStatePort)


@pytest.mark.parametrize(
    "update",
    [
        {"source_pdf": "/different.pdf"},
        {"deck_name": "different"},
        {"source_signature": "sha256:different"},
        {"total_chunks": 3},
    ],
)
def test_manifest_match_rejects_each_incompatible_field(
    tmp_path: Path, update: dict[str, object]
) -> None:
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")

    assert not chunk_resume.manifest_matches_source(
        manifest.model_copy(update=update),
        tmp_path / "source.pdf",
        "deck",
        "sha256:source",
        2,
    )


def test_manifest_match_requires_a_manifest(tmp_path: Path) -> None:
    assert not chunk_resume.manifest_matches_source(
        None, tmp_path / "source.pdf", "deck", "sha256:source", 2
    )


def test_result_path_uses_stable_zero_padded_chunk_names(
    tmp_path: Path,
) -> None:
    assert chunk_resume.get_chunk_result_path(tmp_path, 7) == (
        tmp_path / "chunk_007.json"
    )


def test_prepare_resume_creates_a_fresh_manifest_when_missing(
    tmp_path: Path,
) -> None:
    repository = _repository()
    repository.load_manifest.return_value = None
    pdf_path = tmp_path / "source.pdf"
    resume_dir = tmp_path / "resume"
    state_path = resume_dir / "state.json"

    result = chunk_resume.prepare_resume(
        repository,
        pdf_path,
        "deck",
        "sha256:source",
        2,
        resume_dir,
        state_path,
    )

    assert not result.resumed
    assert result.chunk_decks == {}
    assert result.completed_indexes == set()
    assert result.manifest.source_pdf == str(pdf_path)
    assert result.manifest.source_signature == "sha256:source"
    assert result.manifest.deck_name == "deck"
    assert result.manifest.total_chunks == 2
    assert result.manifest.created_at.tzinfo == UTC
    assert result.manifest.updated_at == result.manifest.created_at
    repository.delete_chunk_results.assert_called_once_with(resume_dir)
    repository.save_manifest.assert_called_once_with(
        state_path, result.manifest
    )


@pytest.mark.parametrize("error", [OSError("read failed"), ValueError("bad")])
def test_prepare_resume_removes_corrupt_manifest_and_results(
    tmp_path: Path, error: Exception
) -> None:
    repository = _repository()
    repository.load_manifest.side_effect = error
    resume_dir = tmp_path / "resume"
    state_path = resume_dir / "state.json"

    result = chunk_resume.prepare_resume(
        repository,
        tmp_path / "source.pdf",
        "deck",
        "sha256:source",
        2,
        resume_dir,
        state_path,
    )

    assert not result.resumed
    repository.delete_manifest.assert_called_once_with(state_path)
    assert repository.delete_chunk_results.call_args_list == [
        call(resume_dir),
        call(resume_dir),
    ]
    repository.save_manifest.assert_called_once_with(
        state_path, result.manifest
    )


def test_prepare_resume_discards_manifest_for_each_incompatible_source_field(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    resume_dir = tmp_path / "resume"
    state_path = resume_dir / "state.json"
    base = _manifest(pdf_path, resume_dir)
    updates = (
        {"source_pdf": str(tmp_path / "other.pdf")},
        {"deck_name": "other"},
        {"source_signature": "sha256:other"},
        {"total_chunks": 1},
    )

    for update in updates:
        repository = _repository()
        repository.load_manifest.return_value = base.model_copy(update=update)

        result = chunk_resume.prepare_resume(
            repository,
            pdf_path,
            "deck",
            "sha256:source",
            2,
            resume_dir,
            state_path,
        )

        assert not result.resumed
        repository.delete_chunk_results.assert_called_once_with(resume_dir)
        repository.save_manifest.assert_called_once_with(
            state_path, result.manifest
        )


def _prepare_resume_with_chunks(
    tmp_path: Path, chunks: list[ChunkState]
) -> tuple[chunk_resume.ResumePreparation, MagicMock, Path]:
    pdf_path = tmp_path / "source.pdf"
    resume_dir = tmp_path / "resume"
    valid_path = chunk_resume.get_chunk_result_path(resume_dir, 1)
    manifest = _manifest(pdf_path, resume_dir, chunks=chunks)
    repository = _repository()
    repository.load_manifest.return_value = manifest
    repository.load_chunk_result.return_value = _deck()

    result = chunk_resume.prepare_resume(
        repository,
        pdf_path,
        "deck",
        "sha256:source",
        2,
        resume_dir,
        resume_dir / "state.json",
    )
    return result, repository, valid_path


def test_prepare_resume_reuses_valid_completed_result(tmp_path: Path) -> None:
    result, repository, valid_path = _prepare_resume_with_chunks(
        tmp_path,
        [
            _state(
                1, chunk_resume.get_chunk_result_path(tmp_path / "resume", 1)
            )
        ],
    )

    assert result.resumed
    assert result.completed_indexes == {1}
    assert result.chunk_decks == {1: repository.load_chunk_result.return_value}
    repository.load_chunk_result.assert_called_once_with(valid_path)


def test_prepare_resume_does_not_load_duplicate_chunk_indexes(
    tmp_path: Path,
) -> None:
    resume_dir = tmp_path / "resume"
    valid_path = chunk_resume.get_chunk_result_path(resume_dir, 1)
    result, repository, _ = _prepare_resume_with_chunks(
        tmp_path, [_state(1, valid_path), _state(1, valid_path)]
    )

    assert result.completed_indexes == set()
    assert result.chunk_decks == {}
    repository.load_chunk_result.assert_not_called()


@pytest.mark.parametrize("chunk_index", [0, 3])
def test_prepare_resume_does_not_load_out_of_range_chunk_indexes(
    tmp_path: Path, chunk_index: int
) -> None:
    resume_dir = tmp_path / "resume"
    path = chunk_resume.get_chunk_result_path(resume_dir, chunk_index)
    result, repository, _ = _prepare_resume_with_chunks(
        tmp_path, [_state(chunk_index, path)]
    )

    assert result.completed_indexes == set()
    assert result.chunk_decks == {}
    repository.load_chunk_result.assert_not_called()


@pytest.mark.parametrize("status", [ChunkStatus.PENDING, ChunkStatus.FAILED])
def test_prepare_resume_does_not_load_non_completed_chunk_states(
    tmp_path: Path, status: ChunkStatus
) -> None:
    resume_dir = tmp_path / "resume"
    path = chunk_resume.get_chunk_result_path(resume_dir, 1)
    result, repository, _ = _prepare_resume_with_chunks(
        tmp_path, [_state(1, path, status=status)]
    )

    assert result.completed_indexes == set()
    assert result.chunk_decks == {}
    repository.load_chunk_result.assert_not_called()


def test_prepare_resume_does_not_load_foreign_result_path(
    tmp_path: Path,
) -> None:
    result, repository, _ = _prepare_resume_with_chunks(
        tmp_path, [_state(1, tmp_path / "foreign.json")]
    )

    assert result.completed_indexes == set()
    assert result.chunk_decks == {}
    repository.load_chunk_result.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("missing result"), ValueError("invalid result")],
)
def test_load_completed_chunks_ignores_unavailable_or_invalid_results(
    tmp_path: Path, error: Exception
) -> None:
    resume_dir = tmp_path / "resume"
    manifest = _manifest(
        tmp_path / "source.pdf",
        resume_dir,
        chunks=[_state(1, chunk_resume.get_chunk_result_path(resume_dir, 1))],
    )
    repository = _repository()
    repository.load_chunk_result.side_effect = error

    decks, indexes = chunk_resume.load_completed_chunks(
        repository, manifest, resume_dir, 2
    )

    assert decks == {}
    assert indexes == set()


def test_load_completed_chunks_rejects_card_count_mismatch(
    tmp_path: Path,
) -> None:
    resume_dir = tmp_path / "resume"
    manifest = _manifest(
        tmp_path / "source.pdf",
        resume_dir,
        chunks=[
            _state(
                1,
                chunk_resume.get_chunk_result_path(resume_dir, 1),
                card_count=2,
            )
        ],
    )
    repository = _repository()
    repository.load_chunk_result.return_value = _deck()

    decks, indexes = chunk_resume.load_completed_chunks(
        repository, manifest, resume_dir, 2
    )

    assert decks == {}
    assert indexes == set()


@pytest.mark.parametrize(
    "missing",
    ["repository", "manifest", "state_path"],
)
def test_mark_chunk_failed_returns_when_resume_state_is_incomplete(
    tmp_path: Path, missing: str
) -> None:
    repository = _repository()
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")
    state_path: Path | None = tmp_path / "resume" / "state.json"
    if missing == "repository":
        repository = None
    elif missing == "manifest":
        manifest = None
    else:
        state_path = None

    chunk_resume.mark_chunk_failed(repository, manifest, state_path, 1, None)

    if repository is not None:
        repository.save_manifest.assert_not_called()


@pytest.mark.parametrize(
    ("error_message", "expected_message"),
    [(None, "Chunk processing failed"), ("quota", "quota")],
)
def test_mark_chunk_failed_updates_state_and_saves_manifest(
    tmp_path: Path,
    error_message: str | None,
    expected_message: str,
) -> None:
    repository = _repository()
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")
    state_path = tmp_path / "resume" / "state.json"

    chunk_resume.mark_chunk_failed(
        repository, manifest, state_path, 2, error_message
    )

    state = manifest.chunks[0]
    assert state.chunk_index == 2
    assert state.status == ChunkStatus.FAILED
    assert state.error_message == expected_message
    assert state.updated_at.tzinfo == UTC
    assert manifest.updated_at == state.updated_at
    repository.save_manifest.assert_called_once_with(state_path, manifest)


def test_mark_chunk_failed_propagates_manifest_save_error(
    tmp_path: Path,
) -> None:
    repository = _repository()
    save_error = OSError("save failed")
    repository.save_manifest.side_effect = save_error
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")
    state_path = tmp_path / "resume" / "state.json"

    with pytest.raises(OSError) as raised:
        chunk_resume.mark_chunk_failed(
            repository, manifest, state_path, 2, "quota"
        )

    assert raised.value is save_error
    state = manifest.chunks[0]
    assert state.chunk_index == 2
    assert state.status == ChunkStatus.FAILED
    assert state.error_message == "quota"
    repository.save_manifest.assert_called_once_with(state_path, manifest)


@pytest.mark.parametrize(
    "missing",
    ["repository", "manifest", "resume_dir", "state_path"],
)
def test_save_chunk_completion_returns_when_resume_state_is_incomplete(
    tmp_path: Path, missing: str
) -> None:
    repository = _repository()
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")
    resume_dir: Path | None = tmp_path / "resume"
    state_path: Path | None = resume_dir / "state.json"
    if missing == "repository":
        repository = None
    elif missing == "manifest":
        manifest = None
    elif missing == "resume_dir":
        resume_dir = None
    else:
        state_path = None

    chunk_resume.save_chunk_completion(
        repository,
        manifest,
        resume_dir,
        state_path,
        1,
        _deck(),
    )

    if repository is not None:
        repository.save_chunk_result.assert_not_called()


@pytest.mark.parametrize("existing_state", [False, True])
def test_save_chunk_completion_persists_in_order_and_upserts_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_state: bool,
) -> None:
    repository = _repository()
    resume_dir = tmp_path / "resume"
    state_path = resume_dir / "state.json"
    result_path = chunk_resume.get_chunk_result_path(resume_dir, 1)
    old_state = _state(1, result_path, status=ChunkStatus.FAILED)
    other_state = _state(
        2,
        chunk_resume.get_chunk_result_path(resume_dir, 2),
        status=ChunkStatus.FAILED,
    )
    manifest = _manifest(
        tmp_path / "source.pdf",
        resume_dir,
        chunks=[old_state] if existing_state else [other_state],
    )
    events: list[str] = []
    repository.save_chunk_result.side_effect = lambda _path, _deck: (
        events.append("save_result")
    )
    repository.save_manifest.side_effect = lambda _path, _manifest: (
        events.append("save_manifest")
    )
    set_state = chunk_resume._set_chunk_state

    def capture_state(*args: object, **kwargs: object) -> None:
        events.append("update_state")
        set_state(*args, **kwargs)

    monkeypatch.setattr(chunk_resume, "_set_chunk_state", capture_state)
    deck = _deck()

    chunk_resume.save_chunk_completion(
        repository, manifest, resume_dir, state_path, 1, deck
    )

    assert events == ["save_result", "update_state", "save_manifest"]
    assert len(manifest.chunks) == (1 if existing_state else 2)
    state = next(item for item in manifest.chunks if item.chunk_index == 1)
    assert state.status == ChunkStatus.COMPLETED
    assert state.card_count == len(deck.flashcards)
    assert state.result_path == str(result_path)
    assert state.updated_at.tzinfo == UTC
    assert manifest.updated_at == state.updated_at
    repository.save_chunk_result.assert_called_once_with(result_path, deck)
    repository.save_manifest.assert_called_once_with(state_path, manifest)


def test_save_chunk_completion_does_not_update_state_if_result_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository()
    repository.save_chunk_result.side_effect = OSError("disk full")
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")
    set_state = MagicMock()
    monkeypatch.setattr(chunk_resume, "_set_chunk_state", set_state)

    with pytest.raises(OSError, match="disk full"):
        chunk_resume.save_chunk_completion(
            repository,
            manifest,
            tmp_path / "resume",
            tmp_path / "resume" / "state.json",
            1,
            _deck(),
        )

    set_state.assert_not_called()
    repository.save_manifest.assert_not_called()


def test_save_chunk_completion_propagates_manifest_save_failure_after_update(
    tmp_path: Path,
) -> None:
    repository = _repository()
    repository.save_manifest.side_effect = OSError("manifest save failed")
    manifest = _manifest(tmp_path / "source.pdf", tmp_path / "resume")

    with pytest.raises(OSError, match="manifest save failed"):
        chunk_resume.save_chunk_completion(
            repository,
            manifest,
            tmp_path / "resume",
            tmp_path / "resume" / "state.json",
            1,
            _deck(),
        )

    assert manifest.chunks[0].status == ChunkStatus.COMPLETED
    repository.save_chunk_result.assert_called_once()
