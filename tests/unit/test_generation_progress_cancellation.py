"""Focused generation progress and cooperative cancellation tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.application.contracts import (
    CancellationToken,
    ProgressEvent,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import (
    GenerateFlashcardsUseCase,
    _ChunkRun,
)
from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.domain.exceptions import OperationCancelled
from flashcards_generator.infrastructure.chunk_state_repository import (
    FileSystemChunkStateRepository,
)


class RecordingReporter:
    """Collect structured progress events in publication order."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


def _write_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as output:
        writer.write(output)


def test_regular_generation_reports_workflow_boundaries(
    temp_dirs, mock_generator, sample_flashcards
) -> None:
    input_dir, output_dir = temp_dirs
    source = input_dir / "lesson.pdf"
    _write_pdf(source)
    reporter = RecordingReporter()
    use_case = GenerateFlashcardsUseCase(
        generator=mock_generator(flashcards=sample_flashcards)
    )
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)

    decks = use_case.execute(
        GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        ),
        reporter=reporter,
    )

    transitions = {(event.stage, event.state) for event in reporter.events}
    expected = {
        (ProgressStage.DISCOVERY, ProgressState.STARTED),
        (ProgressStage.DISCOVERY, ProgressState.COMPLETED),
        (ProgressStage.SOURCE, ProgressState.STARTED),
        (ProgressStage.SOURCE, ProgressState.COMPLETED),
        (ProgressStage.GENERATION, ProgressState.STARTED),
        (ProgressStage.GENERATION, ProgressState.COMPLETED),
        (ProgressStage.EXPORT, ProgressState.STARTED),
        (ProgressStage.EXPORT, ProgressState.COMPLETED),
    }
    cleanup = [(event.stage, event.state) for event in reporter.events[-2:]]
    assert (
        len(decks) == 1
        and expected <= transitions
        and cleanup
        == [
            (ProgressStage.CLEANUP, ProgressState.STARTED),
            (ProgressStage.CLEANUP, ProgressState.COMPLETED),
        ]
    )


def test_cancelled_inter_chunk_wait_preserves_completed_resume_chunk(
    tmp_path: Path,
) -> None:
    repository = FileSystemChunkStateRepository()
    token = CancellationToken()
    reporter = RecordingReporter()
    use_case = GenerateFlashcardsUseCase(
        generator=MagicMock(), chunk_state_repository=repository
    )
    use_case._token = token
    use_case._reporter = reporter
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    chunks = [tmp_path / "chunk1.pdf", tmp_path / "chunk2.pdf"]
    for chunk in chunks:
        chunk.touch()
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=output_dir,
        resume=True,
    )
    run = _ChunkRun(
        pdf_path=source,
        deck_name="source",
        pdf_output_path=output_dir,
        processing_path=source,
        request=request,
        chunks=chunks,
    )
    use_case._prepare_resume(run)
    completed = Deck(
        name="chunk1",
        flashcards=[Flashcard(front="A useful fact", back="A useful answer")],
    )
    use_case._process_chunk = MagicMock(return_value=completed)

    def cancel_between_chunks(_timeout: float) -> None:
        token.cancel()
        token.raise_if_cancelled()

    token.wait_or_cancel = cancel_between_chunks  # type: ignore[method-assign]

    with pytest.raises(OperationCancelled):
        use_case._process_chunks(run)

    manifest = repository.load_manifest(run.state_path)
    assert manifest is not None
    assert (
        len(manifest.chunks),
        manifest.chunks[0].chunk_index,
        manifest.chunks[0].card_count,
    ) == (1, 1, 1)
    use_case._process_chunk.assert_called_once()
    assert any(
        event.stage == ProgressStage.CHUNK
        and event.state == ProgressState.COMPLETED
        and event.chunk_index == 1
        for event in reporter.events
    )


@patch("flashcards_generator.adapters.notebooklm_adapter.subprocess.Popen")
def test_cancelled_chunk_generation_deletes_notebook_and_reaps_commands(
    mock_popen: MagicMock, tmp_path: Path
) -> None:
    token = CancellationToken()

    def process(stdout: str = "") -> MagicMock:
        command = MagicMock()
        command.returncode = 0
        command.communicate.return_value = (stdout, "")
        return command

    create = process('{"id": "notebook-1"}')
    add_source = process('{"source_id": "source-1"}')
    wait_for_source = process()
    generate = process()
    delete = process()

    def cancel_during_generation(*, timeout: int) -> tuple[str, str]:
        assert timeout == 900
        token.cancel()
        return "", ""

    generate.communicate.side_effect = cancel_during_generation
    generate.wait.return_value = None
    commands = [create, add_source, wait_for_source, generate, delete]
    mock_popen.side_effect = commands
    adapter = NotebookLMAdapter("notebooklm")
    use_case = GenerateFlashcardsUseCase(generator=adapter)
    chunk_path = tmp_path / "chunk.pdf"
    chunk_path.touch()
    output_path = tmp_path / "output"
    output_path.mkdir()
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=output_path,
    )

    with (
        adapter.cancellation_scope(token),
        pytest.raises(OperationCancelled),
    ):
        use_case._process_chunk_internal(
            chunk_path,
            "deck",
            output_path,
            request,
            chunk_index=1,
            total_chunks=1,
        )

    assert mock_popen.call_count == 5
    assert all(command.communicate.call_count == 1 for command in commands)
