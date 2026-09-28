from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.domain_models.exceptions import OperationCancelled
from flashcards_generator.integrations.pdf_utils import PDFChunker
from flashcards_generator.services import (
    generation_chunk_orchestration,
)
from flashcards_generator.services import (
    use_cases as use_cases_module,
)
from flashcards_generator.services.contracts import (
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.generation_models import (
    _ChunkRun,
    _ChunkTask,
)
from tests.fixtures.use_case_fixtures import make_use_case


class RecordingChunkContext:
    def __init__(self, results: dict[int, Deck | None]) -> None:
        self.pdf_chunker = PDFChunker()
        self._last_chunk_error_message: str | None = "previous error"
        self.results = results
        self.calls: list[str] = []
        self.cancel_checks = 0
        self.cancel_on_check: int | None = None
        self.cancel_during_wait = False
        self.chunk_delay_seconds = 0.25
        self.combined_deck = Deck(name="combined")

    def _prepare_resume(self, run: _ChunkRun) -> None:
        self.calls.append("prepare")

    def _process_chunks(self, run: _ChunkRun) -> bool:
        return generation_chunk_orchestration.process_chunks(
            self, run, self.chunk_delay_seconds
        )

    def _combine_chunk_decks(self, run: _ChunkRun) -> Deck:
        self.calls.append("combine")
        return self.combined_deck

    def _raise_if_cancelled(self) -> None:
        self.calls.append("cancel-check")
        self.cancel_checks += 1
        if self.cancel_on_check == self.cancel_checks:
            raise OperationCancelled

    def _get_or_process_chunk(
        self, run: _ChunkRun, chunk_index: int, chunk_path: Path
    ) -> Deck | None:
        task = _ChunkTask(
            chunk_path=chunk_path,
            deck_name=run.deck_name,
            pdf_output_path=run.pdf_output_path,
            request=run.request,
            chunk_index=chunk_index,
            total_chunks=len(run.chunks),
        )
        return generation_chunk_orchestration.get_or_process_chunk(
            self, run, task
        )

    def _log_chunk_result(
        self, chunk_index: int, total_chunks: int, chunk_deck: Deck
    ) -> None:
        self.calls.append(f"log:{chunk_index}")
        generation_chunk_orchestration.log_chunk_result(
            chunk_index, total_chunks, chunk_deck
        )

    def _wait_or_cancel(self, timeout: float) -> None:
        self.calls.append(f"wait:{timeout}")
        if self.cancel_during_wait:
            raise OperationCancelled

    def _process_chunk(
        self,
        chunk_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        chunk_index: int,
        total_chunks: int,
    ) -> Deck | None:
        self.calls.append(f"process:{chunk_index}")
        if chunk_index not in self.results:
            raise AssertionError(f"Unexpected chunk {chunk_index}")
        return self.results[chunk_index]

    def _mark_chunk_failed(self, run: _ChunkRun, chunk_index: int) -> None:
        self.calls.append(f"fail:{chunk_index}")

    def _save_chunk_completion(
        self, run: _ChunkRun, chunk_index: int, chunk_deck: Deck
    ) -> None:
        self.calls.append(f"save:{chunk_index}")

    def _publish(
        self,
        stage: ProgressStage,
        state: ProgressState,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
        source: Path | None = None,
        chunk_index: int | None = None,
        cards: int | None = None,
    ) -> None:
        self.calls.append(f"publish:{state.value}:{chunk_index}")


def _make_run(
    tmp_path: Path,
    chunks: list[Path],
    *,
    resume: bool = True,
    processing_path: Path | None = None,
) -> _ChunkRun:
    source_path = tmp_path / "lesson.pdf"
    output_path = tmp_path / "output"
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=output_path,
        resume=resume,
    )
    return _ChunkRun(
        pdf_path=source_path,
        deck_name="Lesson",
        pdf_output_path=output_path,
        processing_path=processing_path or source_path,
        request=request,
        chunks=chunks,
    )


def test_process_large_pdf_keeps_snapshot_and_resume_cleanup_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    chunks = [tmp_path / "chunk1.pdf"]
    snapshot = tmp_path / "snapshot.pdf"
    context = RecordingChunkContext({1: Deck(name="chunk1")})
    chunk_pdf = MagicMock(return_value=chunks)
    cleanup = MagicMock()
    monkeypatch.setattr(context.pdf_chunker, "chunk_pdf", chunk_pdf)
    monkeypatch.setattr(context.pdf_chunker, "cleanup_chunks", cleanup)

    run = _make_run(tmp_path, chunks, resume=False, processing_path=snapshot)

    result = generation_chunk_orchestration.process_large_pdf(context, run)

    assert result is context.combined_deck
    assert chunk_pdf.call_args.args == (
        snapshot,
        run.pdf_output_path / ".temp_chunks",
    )
    assert context.calls[0] == "prepare"
    assert context.calls[-1] == "combine"
    cleanup.assert_called_once_with(chunks)


def test_process_large_pdf_preserves_chunks_when_resuming(
    tmp_path: Path, monkeypatch
) -> None:
    chunks = [tmp_path / "chunk1.pdf"]
    context = RecordingChunkContext({1: Deck(name="chunk1")})
    monkeypatch.setattr(
        context.pdf_chunker, "chunk_pdf", MagicMock(return_value=chunks)
    )
    cleanup = MagicMock()
    monkeypatch.setattr(context.pdf_chunker, "cleanup_chunks", cleanup)
    run = _make_run(tmp_path, chunks, resume=True)

    generation_chunk_orchestration.process_large_pdf(context, run)

    cleanup.assert_not_called()


def test_process_chunks_reuses_completed_chunk_and_persists_before_completed(
    tmp_path: Path,
) -> None:
    resumed = Deck(name="resumed")
    new = Deck(name="new")
    chunks = [tmp_path / "chunk1.pdf", tmp_path / "chunk2.pdf"]
    context = RecordingChunkContext({2: new})
    run = _make_run(tmp_path, chunks)
    run.completed_indexes.add(1)
    run.chunk_decks[1] = resumed

    result = generation_chunk_orchestration.process_chunks(context, run, 2.5)

    assert result
    assert "process:1" not in context.calls
    assert "process:2" in context.calls
    assert "wait:2.5" in context.calls
    assert context.calls.index("save:2") < context.calls.index(
        "publish:completed:2"
    )


def test_process_chunks_stops_after_first_failure(tmp_path: Path) -> None:
    chunks = [tmp_path / "chunk1.pdf", tmp_path / "chunk2.pdf"]
    context = RecordingChunkContext({1: None})
    run = _make_run(tmp_path, chunks)

    result = generation_chunk_orchestration.process_chunks(context, run, 1)

    assert not result
    assert "process:1" in context.calls
    assert "fail:1" in context.calls
    assert "publish:failed:1" in context.calls
    assert "process:2" not in context.calls
    assert not any(call.startswith("wait:") for call in context.calls)


def test_process_chunks_does_not_wait_after_last_chunk(tmp_path: Path) -> None:
    chunks = [tmp_path / "chunk1.pdf"]
    context = RecordingChunkContext({1: Deck(name="chunk1")})
    run = _make_run(tmp_path, chunks)

    result = generation_chunk_orchestration.process_chunks(context, run, 1)

    assert result
    assert not any(call.startswith("wait:") for call in context.calls)


def test_process_chunks_observes_cancellation_before_processing(
    tmp_path: Path,
) -> None:
    context = RecordingChunkContext({1: Deck(name="chunk1")})
    context.cancel_on_check = 1
    run = _make_run(tmp_path, [tmp_path / "chunk1.pdf"])

    with pytest.raises(OperationCancelled):
        generation_chunk_orchestration.process_chunks(context, run, 1)

    assert not any(call.startswith("process:") for call in context.calls)


def test_process_chunks_observes_cancellation_between_chunks(
    tmp_path: Path,
) -> None:
    chunks = [tmp_path / "chunk1.pdf", tmp_path / "chunk2.pdf"]
    context = RecordingChunkContext({
        1: Deck(name="chunk1"),
        2: Deck(name="chunk2"),
    })
    context.cancel_during_wait = True
    run = _make_run(tmp_path, chunks)

    with pytest.raises(OperationCancelled):
        generation_chunk_orchestration.process_chunks(context, run, 1)

    assert "save:1" in context.calls
    assert "process:2" not in context.calls


def test_process_chunks_observes_cancellation_before_persistence(
    tmp_path: Path,
) -> None:
    context = RecordingChunkContext({1: Deck(name="chunk1")})
    context.cancel_on_check = 2
    run = _make_run(tmp_path, [tmp_path / "chunk1.pdf"])

    with pytest.raises(OperationCancelled):
        generation_chunk_orchestration.process_chunks(context, run, 1)

    assert "process:1" in context.calls
    assert "save:1" not in context.calls
    assert "publish:completed:1" not in context.calls


def test_facade_passes_current_chunk_delay_to_extracted_processor(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    context = make_use_case(generator=mock_generator())
    run = _make_run(tmp_path, [])
    processor = MagicMock(return_value=True)
    monkeypatch.setattr(
        generation_chunk_orchestration, "process_chunks", processor
    )
    monkeypatch.setattr(use_cases_module, "CHUNK_DELAY_SECONDS", 0.75)

    assert context._process_chunks(run)

    processor.assert_called_once_with(context, run, 0.75)
