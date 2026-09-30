from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from flashcards_generator.domain_models.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
)
from flashcards_generator.services.ports import ChunkStatePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResumePreparation:
    manifest: ChunkResumeManifest
    chunk_decks: dict[int, Deck]
    completed_indexes: set[int]
    resumed: bool


def get_chunk_result_path(resume_dir: Path, chunk_index: int) -> Path:
    return resume_dir / f"chunk_{chunk_index:03d}.json"


def manifest_matches_source(
    manifest: ChunkResumeManifest | None,
    pdf_path: Path,
    deck_name: str,
    source_signature: str,
    total_chunks: int,
    single_cloze: bool,
) -> bool:
    if manifest is None:
        return False
    return (
        manifest.source_pdf,
        manifest.deck_name,
        manifest.source_signature,
        manifest.total_chunks,
        manifest.single_cloze,
    ) == (
        str(pdf_path),
        deck_name,
        source_signature,
        total_chunks,
        single_cloze,
    )


def prepare_resume(
    repository: ChunkStatePort,
    pdf_path: Path,
    deck_name: str,
    source_signature: str,
    total_chunks: int,
    single_cloze: bool,
    resume_dir: Path,
    state_path: Path,
) -> ResumePreparation:
    existing_manifest = _load_resume_manifest(
        repository, state_path, resume_dir
    )
    if manifest_matches_source(
        existing_manifest,
        pdf_path,
        deck_name,
        source_signature,
        total_chunks,
        single_cloze,
    ):
        assert existing_manifest is not None
        chunk_decks, completed_indexes = load_completed_chunks(
            repository, existing_manifest, resume_dir, total_chunks
        )
        return ResumePreparation(
            existing_manifest,
            chunk_decks,
            completed_indexes,
            True,
        )

    repository.delete_chunk_results(resume_dir)
    manifest = _build_resume_manifest(
        pdf_path,
        deck_name,
        total_chunks,
        source_signature,
        single_cloze,
    )
    repository.save_manifest(state_path, manifest)
    return ResumePreparation(manifest, {}, set(), False)


def load_completed_chunks(
    repository: ChunkStatePort,
    manifest: ChunkResumeManifest,
    resume_dir: Path,
    total_chunks: int,
) -> tuple[dict[int, Deck], set[int]]:
    index_counts = _count_manifest_indexes(manifest)
    chunk_decks: dict[int, Deck] = {}
    completed_indexes: set[int] = set()
    for chunk_state in manifest.chunks:
        chunk_index = chunk_state.chunk_index
        expected_path = get_chunk_result_path(resume_dir, chunk_index)
        if not _is_valid_completed_chunk(
            chunk_state, expected_path, index_counts, total_chunks
        ):
            _warn_for_invalid_completed_chunk(chunk_state)
            continue

        chunk_deck = _load_valid_chunk_result(
            repository, expected_path, chunk_state.card_count, chunk_index
        )
        if chunk_deck is None:
            continue

        chunk_decks[chunk_index] = chunk_deck
        completed_indexes.add(chunk_index)
    return chunk_decks, completed_indexes


def mark_chunk_failed(
    repository: ChunkStatePort | None,
    manifest: ChunkResumeManifest | None,
    state_path: Path | None,
    chunk_index: int,
    error_message: str | None,
) -> None:
    if manifest is None or state_path is None or repository is None:
        return

    _set_chunk_state(
        manifest,
        chunk_index,
        ChunkStatus.FAILED,
        error_message=error_message or "Chunk processing failed",
    )
    repository.save_manifest(state_path, manifest)


def save_chunk_completion(
    repository: ChunkStatePort | None,
    manifest: ChunkResumeManifest | None,
    resume_dir: Path | None,
    state_path: Path | None,
    chunk_index: int,
    chunk_deck: Deck,
) -> None:
    if (
        manifest is None
        or resume_dir is None
        or state_path is None
        or repository is None
    ):
        return

    result_path = get_chunk_result_path(resume_dir, chunk_index)
    repository.save_chunk_result(result_path, chunk_deck)
    _set_chunk_state(
        manifest,
        chunk_index,
        ChunkStatus.COMPLETED,
        card_count=len(chunk_deck.flashcards),
        result_path=result_path,
    )
    repository.save_manifest(state_path, manifest)


def _load_resume_manifest(
    repository: ChunkStatePort,
    state_path: Path,
    resume_dir: Path,
) -> ChunkResumeManifest | None:
    try:
        return repository.load_manifest(state_path)
    except (OSError, ValueError) as error:
        logger.warning("Discarding corrupt resume manifest: %s", error)
        repository.delete_manifest(state_path)
        repository.delete_chunk_results(resume_dir)
        return None


def _count_manifest_indexes(
    manifest: ChunkResumeManifest,
) -> dict[int, int]:
    index_counts: dict[int, int] = {}
    for chunk_state in manifest.chunks:
        index_counts[chunk_state.chunk_index] = (
            index_counts.get(chunk_state.chunk_index, 0) + 1
        )
    return index_counts


def _is_valid_completed_chunk(
    chunk_state: ChunkState,
    expected_path: Path,
    index_counts: dict[int, int],
    total_chunks: int,
) -> bool:
    return (
        index_counts[chunk_state.chunk_index] == 1
        and 1 <= chunk_state.chunk_index <= total_chunks
        and chunk_state.status == ChunkStatus.COMPLETED
        and chunk_state.result_path == str(expected_path)
    )


def _warn_for_invalid_completed_chunk(chunk_state: ChunkState) -> None:
    if chunk_state.status == ChunkStatus.COMPLETED:
        logger.warning(
            "Ignoring invalid saved result for chunk %s",
            chunk_state.chunk_index,
        )


def _load_valid_chunk_result(
    repository: ChunkStatePort,
    result_path: Path,
    expected_card_count: int,
    chunk_index: int,
) -> Deck | None:
    try:
        chunk_deck = repository.load_chunk_result(result_path)
        if len(chunk_deck.flashcards) != expected_card_count:
            raise ValueError("saved card count does not match manifest")
    except (OSError, ValueError) as error:
        logger.warning(
            "Regenerating unavailable result for chunk %s: %s",
            chunk_index,
            error,
        )
        return None
    return chunk_deck


def _build_resume_manifest(
    pdf_path: Path,
    deck_name: str,
    total_chunks: int,
    source_signature: str,
    single_cloze: bool,
) -> ChunkResumeManifest:
    now = datetime.now(UTC)
    return ChunkResumeManifest(
        source_pdf=str(pdf_path),
        source_signature=source_signature,
        deck_name=deck_name,
        total_chunks=total_chunks,
        single_cloze=single_cloze,
        chunks=[],
        created_at=now,
        updated_at=now,
    )


def _set_chunk_state(
    manifest: ChunkResumeManifest,
    chunk_index: int,
    status: ChunkStatus,
    *,
    card_count: int = 0,
    result_path: Path | None = None,
    error_message: str | None = None,
) -> None:
    now = datetime.now(UTC)
    state = ChunkState(
        chunk_index=chunk_index,
        status=status,
        card_count=card_count,
        result_path=str(result_path) if result_path else None,
        updated_at=now,
        error_message=error_message,
    )

    for index, existing_state in enumerate(manifest.chunks):
        if existing_state.chunk_index == chunk_index:
            manifest.chunks[index] = state
            break
    else:
        manifest.chunks.append(state)

    manifest.updated_at = now
