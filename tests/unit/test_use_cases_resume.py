"""Resume-flow tests for chunked PDF processing use cases."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import (
    GenerateFlashcardsUseCase,
)
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


class TestGenerateFlashcardsUseCaseResume:
    def test_resume_false_keeps_current_behavior(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = GenerateFlashcardsUseCase(
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
            "flashcards_generator.application.use_cases.time.sleep",
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
        use_case = GenerateFlashcardsUseCase(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.application.use_cases.time.sleep",
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
        assert {chunk.chunk_index for chunk in saved_manifest.chunks} == {1, 2}
        assert all(
            chunk.status == ChunkStatus.COMPLETED
            for chunk in saved_manifest.chunks
        )

    def test_saved_chunk_decks_are_loaded_and_reused(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        use_case = GenerateFlashcardsUseCase(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.application.use_cases.time.sleep",
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
        use_case = GenerateFlashcardsUseCase(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        use_case._process_chunk = MagicMock(return_value=None)
        monkeypatch.setattr(
            "flashcards_generator.application.use_cases.time.sleep",
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

        assert result is None
        assert use_case._process_chunk.call_count == 1
        assert saved_manifest is not None
        assert len(saved_manifest.chunks) == 1
        assert saved_manifest.chunks[0].chunk_index == 1
        assert saved_manifest.chunks[0].status == ChunkStatus.FAILED
        assert (
            saved_manifest.chunks[0].error_message == "Chunk processing failed"
        )

    def test_stale_signature_causes_fresh_processing(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=2)
        repository = FileSystemChunkStateRepository()
        use_case = GenerateFlashcardsUseCase(
            generator=mock_generator(),
            chunk_state_repository=repository,
        )
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter(chunk_paths)
        )
        monkeypatch.setattr(
            "flashcards_generator.application.use_cases.time.sleep",
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

        assert result is not None
        assert use_case._process_chunk.call_count == 2
        assert [card.front for card in result.flashcards] == [
            "Lambda calculus reductions contains enough context for a valid flashcard.",
            "Vector embeddings semantics contains enough context for a valid flashcard.",
        ]
        assert saved_manifest is not None
        assert (
            saved_manifest.source_signature
            == use_case._compute_source_signature(pdf_path)
        )
        assert len(saved_manifest.chunks) == 2

    def test_successful_completion_flow_cleans_resume_state(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir, pdf_path = _create_large_pdf(temp_dirs)
        chunk_paths = _create_chunk_files(output_dir, total=1)
        repository = FileSystemChunkStateRepository()
        use_case = GenerateFlashcardsUseCase(
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
            "flashcards_generator.application.use_cases.time.sleep",
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
