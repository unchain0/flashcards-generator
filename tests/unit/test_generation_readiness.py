import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter

from flashcards_generator.domain_models.exceptions import OperationCancelled
from flashcards_generator.services.contracts import (
    CancellationToken,
    ProgressEvent,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.use_cases import (
    CHUNK_RETRY_INITIAL_DELAY,
    CHUNK_RETRY_MAX_ATTEMPTS,
)
from tests.fixtures.use_case_fixtures import make_use_case


class RecordingReporter:
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


@pytest.mark.parametrize("mode", ["regular", "chunk"])
@pytest.mark.parametrize("failed_wait", ["source", "artifact"])
def test_failed_readiness_wait_never_completes_or_exports(
    tmp_path: Path,
    mock_generator,
    sample_flashcards,
    mode: str,
    failed_wait: str,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    source_path = input_dir / "lesson.pdf"
    _write_pdf(source_path)

    generator = mock_generator(flashcards=sample_flashcards)
    generator.wait_for_source = MagicMock(return_value=failed_wait != "source")
    generator.generate_flashcards = MagicMock(
        wraps=generator.generate_flashcards
    )
    generator.wait_for_artifact = MagicMock(
        return_value=failed_wait != "artifact"
    )
    generator.download_flashcards = MagicMock(
        wraps=generator.download_flashcards
    )
    generator.parse_flashcards = MagicMock(wraps=generator.parse_flashcards)

    use_case = make_use_case(generator=generator)
    if mode == "chunk":
        chunk_path = output_dir / ".temp_chunks" / "lesson_chunk_001.pdf"
        chunk_path.parent.mkdir(parents=True)
        chunk_path.touch()
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter([chunk_path])
        )
        use_case._wait_or_cancel = MagicMock()
    else:
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)

    reporter = RecordingReporter()
    result = use_case.execute(
        GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        ),
        reporter=reporter,
    )

    attempts = CHUNK_RETRY_MAX_ATTEMPTS if mode == "chunk" else 1
    assert result == []
    assert use_case.last_run_had_errors
    assert generator.wait_for_source.call_count == attempts
    assert generator.generate_flashcards.call_count == (
        0 if failed_wait == "source" else attempts
    )
    assert generator.wait_for_artifact.call_count == (
        0 if failed_wait == "source" else attempts
    )
    generator.download_flashcards.assert_not_called()
    generator.parse_flashcards.assert_not_called()
    assert list(output_dir.rglob("*.csv")) == []
    source_failures = [
        event
        for event in reporter.events
        if event.stage == ProgressStage.SOURCE
        and event.state == ProgressState.FAILED
    ]
    assert len(source_failures) == 1
    assert not any(
        event.stage == ProgressStage.GENERATION
        and event.state == ProgressState.COMPLETED
        for event in reporter.events
    )
    assert not any(
        event.stage == ProgressStage.EXPORT
        and event.state == ProgressState.COMPLETED
        for event in reporter.events
    )


def test_artifact_timeout_does_not_block_a_later_retry(
    tmp_path: Path,
    mock_generator,
    sample_flashcards,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_pdf(input_dir / "lesson.pdf")

    generator = mock_generator(flashcards=sample_flashcards)
    generator.wait_for_artifact = MagicMock(side_effect=[False, True])
    generator.generate_flashcards = MagicMock(
        wraps=generator.generate_flashcards
    )
    generator.download_flashcards = MagicMock(
        wraps=generator.download_flashcards
    )
    use_case = make_use_case(generator=generator)
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
    request = GenerateFlashcardsRequest(
        input_dir=input_dir,
        output_dir=output_dir,
    )

    first_result = use_case.execute(request)
    assert first_result == []
    assert use_case.last_run_had_errors
    assert not (output_dir / "lesson.csv").exists()
    generator.download_flashcards.assert_not_called()

    second_result = use_case.execute(request)

    assert len(second_result) == 1
    assert not use_case.last_run_had_errors
    assert (output_dir / "lesson.csv").exists()
    assert generator.generate_flashcards.call_count == 2
    assert generator.download_flashcards.call_count == 1


@pytest.mark.parametrize("mode", ["regular", "chunk"])
@pytest.mark.parametrize("failed_wait", ["source", "artifact"])
def test_cancellation_during_false_wait_stops_the_attempt(
    tmp_path: Path,
    mock_generator,
    sample_flashcards,
    mode: str,
    failed_wait: str,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_pdf(input_dir / "lesson.pdf")

    token = CancellationToken()
    generator = mock_generator(flashcards=sample_flashcards)
    generator.generate_flashcards = MagicMock(
        wraps=generator.generate_flashcards
    )
    generator.wait_for_source = MagicMock(wraps=generator.wait_for_source)
    generator.wait_for_artifact = MagicMock(wraps=generator.wait_for_artifact)
    generator.download_flashcards = MagicMock(
        wraps=generator.download_flashcards
    )
    generator.parse_flashcards = MagicMock(wraps=generator.parse_flashcards)
    if failed_wait == "source":
        generator.wait_for_source.side_effect = lambda *_args, **_kwargs: (
            token.cancel() or False
        )
    else:
        generator.wait_for_artifact.side_effect = lambda *_args, **_kwargs: (
            token.cancel() or False
        )

    use_case = make_use_case(generator=generator)
    if mode == "chunk":
        chunk_path = output_dir / ".temp_chunks" / "lesson_chunk_001.pdf"
        chunk_path.parent.mkdir(parents=True)
        chunk_path.touch()
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
        use_case.pdf_chunker.chunk_pdf = MagicMock(
            return_value=iter([chunk_path])
        )
        use_case._wait_or_cancel = MagicMock()
    else:
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)

    with pytest.raises(OperationCancelled):
        use_case.execute(
            GenerateFlashcardsRequest(
                input_dir=input_dir,
                output_dir=output_dir,
            ),
            token=token,
        )

    assert generator.wait_for_source.call_count == 1
    assert generator.generate_flashcards.call_count == (
        0 if failed_wait == "source" else 1
    )
    assert generator.wait_for_artifact.call_count == (
        0 if failed_wait == "source" else 1
    )
    generator.download_flashcards.assert_not_called()
    generator.parse_flashcards.assert_not_called()
    assert list(output_dir.rglob("*.csv")) == []


def test_chunk_source_timeout_retries_before_final_export(
    tmp_path: Path,
    mock_generator,
    sample_flashcards,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_pdf(input_dir / "lesson.pdf")
    chunk_path = output_dir / ".temp_chunks" / "lesson_chunk_001.pdf"
    chunk_path.parent.mkdir(parents=True)
    chunk_path.touch()

    generator = mock_generator(flashcards=sample_flashcards)
    generator.wait_for_source = MagicMock(side_effect=[False, True])
    generator.generate_flashcards = MagicMock(
        wraps=generator.generate_flashcards
    )
    generator.wait_for_artifact = MagicMock(wraps=generator.wait_for_artifact)
    generator.download_flashcards = MagicMock(
        wraps=generator.download_flashcards
    )
    generator.parse_flashcards = MagicMock(wraps=generator.parse_flashcards)
    use_case = make_use_case(generator=generator)
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
    use_case.pdf_chunker.chunk_pdf = MagicMock(return_value=iter([chunk_path]))
    delays: list[float] = []
    use_case._wait_or_cancel = MagicMock(side_effect=delays.append)
    use_case.exporter.export_csv = MagicMock(
        wraps=use_case.exporter.export_csv
    )
    reporter = RecordingReporter()

    result = use_case.execute(
        GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        ),
        reporter=reporter,
    )

    csv_path = output_dir / "lesson.csv"
    assert len(result) == 1
    assert generator.wait_for_source.call_count == 2
    assert generator.generate_flashcards.call_count == 1
    assert generator.wait_for_artifact.call_count == 1
    assert generator.download_flashcards.call_count == 1
    assert generator.parse_flashcards.call_count == 1
    use_case.exporter.export_csv.assert_called_once()
    assert delays == [CHUNK_RETRY_INITIAL_DELAY]
    assert _read_csv_rows(csv_path) == [
        [card.front, card.back] for card in result[0].flashcards
    ]
    assert (
        sum(
            event.stage == ProgressStage.CHUNK
            and event.state == ProgressState.RETRYING
            for event in reporter.events
        )
        == 1
    )
    assert (
        sum(
            event.stage == ProgressStage.GENERATION
            and event.state == ProgressState.COMPLETED
            for event in reporter.events
        )
        == 1
    )
    assert (
        sum(
            event.stage == ProgressStage.EXPORT
            and event.state == ProgressState.COMPLETED
            for event in reporter.events
        )
        == 1
    )


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as file_obj:
        return list(csv.reader(file_obj))


def test_successfully_completed_empty_deck_is_not_a_timeout(
    tmp_path: Path,
    mock_generator,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_pdf(input_dir / "lesson.pdf")

    generator = mock_generator(flashcards=[])
    use_case = make_use_case(generator=generator)
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
    reporter = RecordingReporter()

    result = use_case.execute(
        GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        ),
        reporter=reporter,
    )

    assert len(result) == 1
    assert result[0].flashcards == []
    assert not use_case.last_run_had_errors
    assert any(
        event.stage == ProgressStage.SOURCE
        and event.state == ProgressState.COMPLETED
        for event in reporter.events
    )
