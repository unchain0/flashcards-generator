"""Resume-flow tests for chunked PDF processing use cases."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call

import pytest

from flashcards_generator.domain_models.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
    Flashcard,
)
from flashcards_generator.integrations.chunk_state_repository import (
    FileSystemChunkStateRepository,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.use_cases import (
    CHUNK_RETRY_MAX_ATTEMPTS,
    GenerateFlashcardsUseCase,
    _ChunkRun,
)
from tests.fixtures.adapter_fixtures import MockFlashcardGenerator
from tests.fixtures.use_case_fixtures import make_use_case

if TYPE_CHECKING:
    from pathlib import Path


def _make_chunk_deck(name: str, front: str) -> Deck:
    return Deck(
        name=name,
        description=name,
        flashcards=[
            Flashcard(
                front=f"{front} contains enough context for a valid flashcard.",
                back=f"Detailed explanation for {front} with enough words.",
            )
        ],
        created_at=datetime.now(UTC),
    )


def _create_large_pdf(temp_dirs) -> tuple[Path, Path, Path]:
    input_dir, output_dir = temp_dirs
    tema_dir = input_dir / "Tema1"
    tema_dir.mkdir()
    pdf_path = tema_dir / "large.pdf"
    pdf_path.write_text("PDF content")
    return input_dir, output_dir, pdf_path


def _create_chunk_files(output_dir: Path, total: int) -> list[Path]:
    temp_dir = output_dir / "Tema1" / ".temp_chunks"
    temp_dir.mkdir(parents=True, exist_ok=True)

    chunk_paths = []
    for index in range(1, total + 1):
        chunk_path = temp_dir / f"large_chunk_{index:03d}.pdf"
        chunk_path.touch()
        chunk_paths.append(chunk_path)

    return chunk_paths


def _build_manifest(
    use_case: GenerateFlashcardsUseCase,
    pdf_path: Path,
    pdf_output_path: Path,
    total_chunks: int,
    *,
    signature: str,
    chunks: list[ChunkState],
) -> ChunkResumeManifest:
    now = datetime.now(UTC)
    return ChunkResumeManifest(
        source_pdf=str(pdf_path),
        source_signature=signature,
        deck_name="Tema1_large",
        total_chunks=total_chunks,
        chunks=chunks,
        created_at=now,
        updated_at=now,
    )


def _assert_completed_chunk_manifest(manifest: ChunkResumeManifest) -> None:
    """Assert both chunks are represented as completed."""
    assert {chunk.chunk_index for chunk in manifest.chunks} == {1, 2}
    assert all(
        chunk.status == ChunkStatus.COMPLETED for chunk in manifest.chunks
    )


def _assert_failed_chunk_manifest(manifest: ChunkResumeManifest) -> None:
    """Assert the first failed chunk is persisted with its reason."""
    assert len(manifest.chunks) == 1
    assert manifest.chunks[0].chunk_index == 1
    assert manifest.chunks[0].status == ChunkStatus.FAILED
    assert manifest.chunks[0].error_message == "Chunk processing failed"


def _assert_fresh_chunk_decks(
    result: Deck | None, process_chunk: MagicMock
) -> None:
    """Assert stale state caused both chunks to be regenerated."""
    assert result is not None
    assert process_chunk.call_count == 2
    assert [card.front for card in result.flashcards] == [
        "Lambda calculus reductions contains enough context for a valid flashcard.",
        "Vector embeddings semantics contains enough context for a valid flashcard.",
    ]


def _assert_fresh_manifest(
    manifest: ChunkResumeManifest | None,
    use_case: GenerateFlashcardsUseCase,
    pdf_path: Path,
) -> None:
    """Assert regenerated state matches the current source signature."""
    assert manifest is not None
    assert manifest.source_signature == use_case._compute_source_signature(
        pdf_path
    )
    assert len(manifest.chunks) == 2


class TestGenerateFlashcardsUseCaseResume:
    def test_resume_false_keeps_current_behavior(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-1", "Fresh card")
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=False,
        )

        result = use_case.execute(request)
        pdf_output_path = output_dir / "Tema1"

        assert len(result) == 1
        assert not use_case._get_resume_dir(
            pdf_output_path, pdf_path.stem
        ).exists()
        assert not use_case._get_state_file_path(
            pdf_output_path, pdf_path.stem
        ).exists()

    def test_completed_chunk_is_skipped_on_restart(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        chunk_one_result = use_case._get_chunk_result_path(resume_dir, 1)
        repository.save_chunk_result(
            chunk_one_result, _make_chunk_deck("chunk-1", "Saved card")
        )
        manifest = _build_manifest(
            use_case,
            pdf_path,
            pdf_output_path,
            total_chunks=2,
            signature=use_case._compute_source_signature(pdf_path),
            chunks=[
                ChunkState(
                    chunk_index=1,
                    status=ChunkStatus.COMPLETED,
                    card_count=1,
                    result_path=str(chunk_one_result),
                    updated_at=datetime.now(UTC),
                )
            ],
        )
        repository.save_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem),
            manifest,
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-2", "New card")
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            request,
        )
        saved_manifest = repository.load_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem)
        )

        assert result is not None
        assert use_case._process_chunk.call_count == 1
        assert use_case._process_chunk.call_args == call(
            chunk_paths[1],
            "Tema1_large",
            pdf_output_path,
            request,
            2,
            2,
        )
        assert saved_manifest is not None
        _assert_completed_chunk_manifest(saved_manifest)

    def test_saved_chunk_decks_are_loaded_and_reused(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        chunk_one_result = use_case._get_chunk_result_path(resume_dir, 1)
        repository.save_chunk_result(
            chunk_one_result,
            _make_chunk_deck("chunk-1", "Persisted neural pathways"),
        )
        manifest = _build_manifest(
            use_case,
            pdf_path,
            pdf_output_path,
            total_chunks=2,
            signature=use_case._compute_source_signature(pdf_path),
            chunks=[
                ChunkState(
                    chunk_index=1,
                    status=ChunkStatus.COMPLETED,
                    card_count=1,
                    result_path=str(chunk_one_result),
                    updated_at=datetime.now(UTC),
                )
            ],
        )
        repository.save_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem),
            manifest,
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck(
                "chunk-2", "Database transaction isolation"
            )
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            request,
        )

        assert result is not None
        assert [card.front for card in result.flashcards] == [
            "Persisted neural pathways contains enough context for a valid flashcard.",
            "Database transaction isolation contains enough context for a valid flashcard.",
        ]

    def test_failed_chunk_is_marked_failed_and_stops_processing(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        use_case._process_chunk = MagicMock(return_value=None)
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        pdf_output_path = output_dir / "Tema1"
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            request,
        )
        saved_manifest = repository.load_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem)
        )

        assert saved_manifest is not None
        assert result is None
        assert use_case._process_chunk.call_count == 1
        _assert_failed_chunk_manifest(saved_manifest)

    def test_source_not_ready_preserves_prior_chunk_for_resume(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        generator = mock_generator(
            flashcards=[
                {
                    "front": (
                        "The {{c1::transaction log}} records changes before "
                        "they reach durable storage."
                    ),
                    "back": "It supports recovery after an interrupted commit.",
                }
            ]
        )
        generator.wait_for_source = MagicMock(
            side_effect=[True, *([False] * CHUNK_RETRY_MAX_ATTEMPTS)]
        )
        generator.generate_flashcards = MagicMock(
            wraps=generator.generate_flashcards
        )
        use_case = make_use_case(
            generator=generator,
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        result = use_case.execute(
            GenerateFlashcardsRequest(
                input_dir=input_dir,
                output_dir=output_dir,
                resume=True,
            )
        )
        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        state_path = use_case._get_state_file_path(
            pdf_output_path, pdf_path.stem
        )
        manifest = repository.load_manifest(state_path)
        first_chunk_result = use_case._get_chunk_result_path(resume_dir, 1)
        second_chunk_result = use_case._get_chunk_result_path(resume_dir, 2)

        assert result == []
        assert manifest is not None
        assert [chunk.status for chunk in manifest.chunks] == [
            ChunkStatus.COMPLETED,
            ChunkStatus.FAILED,
        ]
        assert first_chunk_result.exists()
        assert not second_chunk_result.exists()
        assert generator.wait_for_source.call_count == (
            1 + CHUNK_RETRY_MAX_ATTEMPTS
        )
        generator.generate_flashcards.assert_called_once()
        assert not list(output_dir.rglob("large.csv"))

    def test_stale_signature_causes_fresh_processing(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        stale_chunk_result = use_case._get_chunk_result_path(resume_dir, 1)
        repository.save_chunk_result(
            stale_chunk_result,
            _make_chunk_deck("chunk-1", "Stale front"),
        )
        stale_manifest = _build_manifest(
            use_case,
            pdf_path,
            pdf_output_path,
            total_chunks=2,
            signature="stale-signature",
            chunks=[
                ChunkState(
                    chunk_index=1,
                    status=ChunkStatus.COMPLETED,
                    card_count=1,
                    result_path=str(stale_chunk_result),
                    updated_at=datetime.now(UTC),
                )
            ],
        )
        repository.save_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem),
            stale_manifest,
        )
        use_case._process_chunk = MagicMock(
            side_effect=[
                _make_chunk_deck("chunk-1", "Lambda calculus reductions"),
                _make_chunk_deck("chunk-2", "Vector embeddings semantics"),
            ]
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            request,
        )
        saved_manifest = repository.load_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem)
        )

        _assert_fresh_chunk_decks(result, use_case._process_chunk)
        _assert_fresh_manifest(saved_manifest, use_case, pdf_path)

    def test_successful_completion_flow_cleans_resume_state(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-1", "Final card")
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _seconds: None,
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        result = use_case.execute(request)
        pdf_output_path = output_dir / "Tema1"

        assert len(result) == 1
        assert (pdf_output_path / "large.csv").exists()
        assert not use_case._get_resume_dir(
            pdf_output_path, pdf_path.stem
        ).exists()
        assert not use_case._get_state_file_path(
            pdf_output_path, pdf_path.stem
        ).exists()
        assert not chunk_paths[0].exists()

    def test_missing_completed_result_is_regenerated(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(), chunk_state_repository=repository
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _: None,
        )
        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        missing_result = use_case._get_chunk_result_path(resume_dir, 1)
        repository.save_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem),
            _build_manifest(
                use_case,
                pdf_path,
                pdf_output_path,
                total_chunks=1,
                signature=use_case._compute_source_signature(pdf_path),
                chunks=[
                    ChunkState(
                        chunk_index=1,
                        status=ChunkStatus.COMPLETED,
                        card_count=1,
                        result_path=str(missing_result),
                        updated_at=datetime.now(UTC),
                    )
                ],
            ),
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-1", "Regenerated")
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir, resume=True
            ),
        )

        assert result is not None
        use_case._process_chunk.assert_called_once()

    def test_corrupt_manifest_restarts_processing(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(), chunk_state_repository=repository
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _: None,
        )
        pdf_output_path = output_dir / "Tema1"
        state_path = use_case._get_state_file_path(
            pdf_output_path, pdf_path.stem
        )
        state_path.parent.mkdir(parents=True)
        state_path.write_text("{invalid json")
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-1", "Recovered")
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir, resume=True
            ),
        )

        assert result is not None
        use_case._process_chunk.assert_called_once()
        assert repository.load_manifest(state_path) is not None

    def test_foreign_completed_result_is_regenerated(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(), chunk_state_repository=repository
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _: None,
        )
        pdf_output_path = output_dir / "Tema1"
        foreign_result = output_dir / "foreign.json"
        repository.save_chunk_result(
            foreign_result, _make_chunk_deck("foreign", "Injected")
        )
        repository.save_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem),
            _build_manifest(
                use_case,
                pdf_path,
                pdf_output_path,
                total_chunks=1,
                signature=use_case._compute_source_signature(pdf_path),
                chunks=[
                    ChunkState(
                        chunk_index=1,
                        status=ChunkStatus.COMPLETED,
                        card_count=1,
                        result_path=str(foreign_result),
                        updated_at=datetime.now(UTC),
                    )
                ],
            ),
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-1", "Trusted")
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir, resume=True
            ),
        )

        assert result is not None
        use_case._process_chunk.assert_called_once()
        assert "Injected" not in result.flashcards[0].front

    def test_source_digest_invalidates_same_size_restored_mtime(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(), chunk_state_repository=repository
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _: None,
        )
        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        result_path = use_case._get_chunk_result_path(resume_dir, 1)
        repository.save_chunk_result(
            result_path, _make_chunk_deck("chunk-1", "Stale")
        )
        repository.save_manifest(
            use_case._get_state_file_path(pdf_output_path, pdf_path.stem),
            _build_manifest(
                use_case,
                pdf_path,
                pdf_output_path,
                total_chunks=1,
                signature=use_case._compute_source_signature(pdf_path),
                chunks=[
                    ChunkState(
                        chunk_index=1,
                        status=ChunkStatus.COMPLETED,
                        card_count=1,
                        result_path=str(result_path),
                        updated_at=datetime.now(UTC),
                    )
                ],
            ),
        )
        original_stat = pdf_path.stat()
        pdf_path.write_text("Other text!")
        os.utime(
            pdf_path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        use_case._process_chunk = MagicMock(
            return_value=_make_chunk_deck("chunk-1", "Fresh")
        )

        result = use_case._process_large_pdf(
            pdf_path,
            "Tema1_large",
            pdf_output_path,
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir, resume=True
            ),
        )

        assert result is not None
        use_case._process_chunk.assert_called_once()

    def test_transient_oserror_retries_and_final_failure_is_recorded(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(), chunk_state_repository=repository
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.services.use_cases.time.sleep",
            lambda _: None,
        )
        recovered = _make_chunk_deck("chunk-1", "Recovered after I/O")
        use_case._process_chunk_internal = MagicMock(
            side_effect=[OSError("temporary download failure"), recovered]
        )
        request = GenerateFlashcardsRequest(
            input_dir=input_dir, output_dir=output_dir, resume=True
        )

        assert (
            use_case._process_chunk(
                chunk_paths[0],
                "Tema1_large",
                output_dir / "Tema1",
                request,
                1,
                1,
            )
            == recovered
        )
        assert use_case._process_chunk_internal.call_count == 2

        use_case._process_chunk = MagicMock(return_value=None)
        assert (
            use_case._process_large_pdf(
                pdf_path, "Tema1_large", output_dir / "Tema1", request
            )
            is None
        )
        manifest = repository.load_manifest(
            use_case._get_state_file_path(output_dir / "Tema1", pdf_path.stem)
        )
        assert manifest is not None
        assert manifest.chunks[0].status == ChunkStatus.FAILED

    def test_transient_chunk_error_is_logged_after_retry_limit(
        self,
        temp_dirs: tuple[Path, Path],
        mock_generator: type[MockFlashcardGenerator],
    ) -> None:
        input_dir, output_dir = temp_dirs
        use_case = make_use_case(generator=mock_generator())
        use_case._process_chunk_internal = MagicMock(
            side_effect=OSError("temporary download failure")
        )
        use_case._wait_or_cancel = MagicMock()
        request = GenerateFlashcardsRequest(
            input_dir=input_dir, output_dir=output_dir
        )

        result = use_case._process_chunk(
            input_dir / "chunk.pdf",
            "Deck",
            output_dir,
            request,
            1,
            1,
        )

        assert result is None
        assert (
            use_case._process_chunk_internal.call_count
            == CHUNK_RETRY_MAX_ATTEMPTS
        )
        assert (
            use_case._last_chunk_error_message == "temporary download failure"
        )

    def test_no_result_retry_uses_bounded_backoff_delays(
        self, temp_dirs, mock_generator
    ) -> None:
        input_dir, output_dir = temp_dirs
        use_case = make_use_case(generator=mock_generator())
        use_case._process_chunk_internal = MagicMock(return_value=None)
        delays: list[float] = []
        use_case._wait_or_cancel = MagicMock(side_effect=delays.append)

        result = use_case._process_chunk(
            input_dir / "chunk.pdf",
            "Deck",
            output_dir,
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir
            ),
            1,
            1,
        )

        assert result is None
        assert delays == [5.0, 10.0]
        assert use_case._process_chunk_internal.call_count == 3

    def test_result_persistence_failure_does_not_save_completed_manifest(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        repository = FileSystemChunkStateRepository()
        use_case = make_use_case(
            generator=mock_generator(), chunk_state_repository=repository
        )
        pdf_output_path = output_dir / "Tema1"
        resume_dir = use_case._get_resume_dir(pdf_output_path, pdf_path.stem)
        state_path = use_case._get_state_file_path(
            pdf_output_path, pdf_path.stem
        )
        manifest = _build_manifest(
            use_case,
            pdf_path,
            pdf_output_path,
            total_chunks=1,
            signature=use_case._compute_source_signature(pdf_path),
            chunks=[],
        )

        def fail_save_result(_path: Path, _deck: Deck) -> None:
            raise OSError("result persistence failed")

        monkeypatch.setattr(repository, "save_chunk_result", fail_save_result)
        run = _ChunkRun(
            pdf_path=pdf_path,
            deck_name="Tema1_large",
            pdf_output_path=pdf_output_path,
            processing_path=pdf_path,
            request=GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir, resume=True
            ),
            manifest=manifest,
            resume_dir=resume_dir,
            state_path=state_path,
        )

        with pytest.raises(OSError, match="result persistence failed"):
            use_case._save_chunk_completion(
                run, 1, _make_chunk_deck("chunk-1", "Fresh result")
            )

        assert manifest.chunks == []
        assert not state_path.exists()
