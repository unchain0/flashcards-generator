from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter

from flashcards_generator.domain_models.entities import ChunkStatus
from flashcards_generator.integrations import deck_exporter as exporter_module
from flashcards_generator.integrations.chunk_state_repository import (
    FileSystemChunkStateRepository,
)
from flashcards_generator.services.contracts import (
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
)
from flashcards_generator.services.use_cases import (
    GenerateFlashcardsUseCase,
)
from tests.fixtures.use_case_fixtures import make_use_case


class RecordingReporter(ProgressReporter):
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


def _write_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as file_obj:
        writer.write(file_obj)


def _build_request(
    input_dir: Path,
    output_dir: Path,
    *,
    resume: bool = False,
) -> GenerateFlashcardsRequest:
    return GenerateFlashcardsRequest(
        input_dir=input_dir,
        output_dir=output_dir,
        resume=resume,
    )


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as file_obj:
        return list(csv.reader(file_obj))


def _build_chunked_use_case(
    generator: FlashcardGeneratorPort,
    repository: FileSystemChunkStateRepository,
    chunk_path: Path,
) -> GenerateFlashcardsUseCase:
    use_case = make_use_case(
        generator=generator,
        chunk_state_repository=repository,
    )
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
    use_case.pdf_chunker.chunk_pdf = MagicMock(return_value=iter([chunk_path]))
    return use_case


def test_regular_generation_can_retry_after_csv_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_generator,
    sample_flashcards,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_pdf(input_dir / "lesson.pdf")

    generator = mock_generator(flashcards=sample_flashcards)
    generator.generate_flashcards = MagicMock(
        wraps=generator.generate_flashcards
    )
    use_case = make_use_case(generator=generator)
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
    export_csv = MagicMock(wraps=use_case.exporter.export_csv)
    monkeypatch.setattr(use_case.exporter, "export_csv", export_csv)
    request = _build_request(input_dir, output_dir)
    reporter = RecordingReporter()
    original_convert = exporter_module.convert_to_anki_math_format
    calls = 0

    def fail_after_first_row(text: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected CSV write failure")
        return original_convert(text)

    with monkeypatch.context() as patcher:
        patcher.setattr(
            "flashcards_generator.integrations.deck_exporter.convert_to_anki_math_format",
            fail_after_first_row,
        )
        with pytest.raises(OSError, match="injected CSV write failure"):
            use_case.execute(request, reporter=reporter)

    csv_path = output_dir / "lesson.csv"
    assert not csv_path.exists()
    assert any(
        event.stage == ProgressStage.EXPORT
        and event.state == ProgressState.FAILED
        for event in reporter.events
    )
    assert not any(
        event.stage == ProgressStage.EXPORT
        and event.state == ProgressState.COMPLETED
        for event in reporter.events
    )

    result = use_case.execute(request, reporter=reporter)

    assert len(result) == 1
    assert csv_path.exists()
    assert _read_csv_rows(csv_path) == [
        [card.front, card.back] for card in result[0].flashcards
    ]
    assert generator.generate_flashcards.call_count == 2
    assert export_csv.call_count == 2
    assert (
        sum(
            event.stage == ProgressStage.EXPORT
            and event.state == ProgressState.COMPLETED
            for event in reporter.events
        )
        == 1
    )


def test_resume_reuses_completed_chunks_after_final_csv_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_generator,
    sample_flashcards,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_pdf(input_dir / "lesson.pdf")
    chunk_path = output_dir / ".temp_chunks" / "lesson_chunk_001.pdf"
    _write_pdf(chunk_path)

    repository = FileSystemChunkStateRepository()
    generator = mock_generator(flashcards=sample_flashcards)
    generator.generate_flashcards = MagicMock(
        wraps=generator.generate_flashcards
    )
    first_use_case = _build_chunked_use_case(generator, repository, chunk_path)
    request = _build_request(input_dir, output_dir, resume=True)
    original_convert = exporter_module.convert_to_anki_math_format
    calls = 0

    def fail_after_first_row(text: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected CSV write failure")
        return original_convert(text)

    with monkeypatch.context() as patcher:
        patcher.setattr(
            "flashcards_generator.integrations.deck_exporter.convert_to_anki_math_format",
            fail_after_first_row,
        )
        with pytest.raises(OSError, match="injected CSV write failure"):
            first_use_case.execute(request)

    csv_path = output_dir / "lesson.csv"
    state_path = first_use_case._get_state_file_path(output_dir, "lesson")
    manifest = repository.load_manifest(state_path)
    assert not csv_path.exists()
    assert manifest is not None
    assert manifest.chunks[0].status == ChunkStatus.COMPLETED
    assert manifest.chunks[0].result_path is not None
    assert Path(manifest.chunks[0].result_path).exists()
    assert generator.generate_flashcards.call_count == 1

    resumed_use_case = _build_chunked_use_case(
        generator, repository, chunk_path
    )
    resumed_export_csv = MagicMock(wraps=resumed_use_case.exporter.export_csv)
    monkeypatch.setattr(
        resumed_use_case.exporter, "export_csv", resumed_export_csv
    )
    result = resumed_use_case.execute(request)

    assert len(result) == 1
    assert csv_path.exists()
    assert _read_csv_rows(csv_path) == [
        [card.front, card.back] for card in result[0].flashcards
    ]
    assert generator.generate_flashcards.call_count == 1
    resumed_export_csv.assert_called_once()
    assert repository.load_manifest(state_path) is None
    assert not chunk_path.exists()
