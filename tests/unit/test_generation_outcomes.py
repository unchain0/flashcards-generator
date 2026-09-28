from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter

from flashcards_generator.services.contracts import (
    CancellationToken,
    ProgressEvent,
    ProgressStage,
    ProgressState,
    SourceFailure,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.generation_workflow import (
    UseCaseGenerationWorkflow,
    _OutcomeReporter,
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


def test_outcome_reporter_counts_source_events_and_forwards_in_order() -> None:
    reporter = RecordingReporter()
    outcome_reporter = _OutcomeReporter(reporter)
    source = Path("failed.pdf")
    events = [
        ProgressEvent(
            stage=ProgressStage.DISCOVERY,
            state=ProgressState.COMPLETED,
            message="Discovery complete",
            current=2,
            total=3,
        ),
        ProgressEvent(
            stage=ProgressStage.SOURCE,
            state=ProgressState.COMPLETED,
            message="Source complete",
            source=Path("completed.pdf"),
        ),
        ProgressEvent(
            stage=ProgressStage.SOURCE,
            state=ProgressState.SKIPPED,
            message="Source skipped",
            source=Path("skipped.pdf"),
        ),
        ProgressEvent(
            stage=ProgressStage.CHUNK,
            state=ProgressState.COMPLETED,
            message="Chunk complete",
            chunk_index=1,
        ),
        ProgressEvent(
            stage=ProgressStage.SOURCE,
            state=ProgressState.FAILED,
            message="Source failed",
            source=source,
        ),
    ]

    for event in events:
        outcome_reporter.publish(event)

    assert reporter.events == events
    assert outcome_reporter.discovered == 3
    assert outcome_reporter.completed == 1
    assert outcome_reporter.skipped == 1
    assert outcome_reporter.failures == [
        SourceFailure(source=source, reason="Source failed")
    ]


@pytest.mark.parametrize(
    ("source", "expected_source"),
    [
        (Path("lesson.pdf"), Path("lesson.pdf")),
        (None, Path("<unknown>")),
    ],
)
def test_failed_source_preserves_path_and_reason(
    source: Path | None,
    expected_source: Path,
) -> None:
    reporter = RecordingReporter()
    outcome_reporter = _OutcomeReporter(reporter)
    event = ProgressEvent(
        stage=ProgressStage.SOURCE,
        state=ProgressState.FAILED,
        message="Could not read source",
        source=source,
    )

    outcome_reporter.publish(event)

    assert outcome_reporter.failures == [
        SourceFailure(source=expected_source, reason="Could not read source")
    ]
    assert reporter.events == [event]


def test_csv_snapshot_of_missing_directory_does_not_create_it(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "not-created"

    assert UseCaseGenerationWorkflow._csv_snapshot(output_dir) == {}
    assert not output_dir.exists()


def test_timeout_is_failed_while_later_source_completes(
    tmp_path: Path,
    mock_generator,
    sample_flashcards,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    first_source = input_dir / "a_timeout.pdf"
    second_source = input_dir / "b_valid.pdf"
    _write_pdf(first_source)
    _write_pdf(second_source)

    generator = mock_generator(flashcards=sample_flashcards)
    generator.wait_for_artifact = MagicMock(side_effect=[False, True])
    use_case = make_use_case(generator=generator)
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
    workflow = UseCaseGenerationWorkflow(lambda _timeout: use_case)

    outcome = workflow.generate(
        GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        ),
        reporter=RecordingReporter(),
        token=CancellationToken(),
    )

    assert outcome.discovered_sources == 2
    assert outcome.completed_sources == 1
    assert outcome.skipped_sources == 0
    assert not outcome.succeeded
    assert [failure.source for failure in outcome.failed_sources] == [
        first_source
    ]
    assert len(outcome.decks) == 1
    assert outcome.decks[0].name == "b_valid"
    assert outcome.csv_paths == (output_dir / "b_valid.csv",)
    assert not (output_dir / "a_timeout.csv").exists()
    assert (output_dir / "b_valid.csv").exists()
