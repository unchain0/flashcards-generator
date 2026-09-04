"""Unit tests for framework-neutral application contracts."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from flashcards_generator.application.contracts import (
    GenerationOutcome,
    NullProgressReporter,
    ProgressEvent,
    ProgressStage,
    ProgressState,
    SourceFailure,
)
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.dto.workflow import (
    AnkiExportOptions,
    AuthStatus,
    CleanupOutcome,
    CleanupRequest,
    GenerateWorkflowRequest,
    MergeOutcome,
)
from flashcards_generator.domain.entities import Deck


def test_progress_event_is_immutable_and_framework_neutral() -> None:
    event = ProgressEvent(
        stage=ProgressStage.CHUNK,
        state=ProgressState.ADVANCED,
        message="chunk complete",
        current=2,
        total=4,
        source=Path("notes.pdf"),
        chunk_index=1,
        cards=12,
    )

    assert event.current == 2
    assert event.stage.value == "chunk"
    with pytest.raises(FrozenInstanceError):
        event.current = 3  # type: ignore[misc]


def test_null_progress_reporter_accepts_events() -> None:
    reporter = NullProgressReporter()

    reporter.publish(
        ProgressEvent(
            stage=ProgressStage.DISCOVERY,
            state=ProgressState.STARTED,
            message="discovering",
        )
    )


def test_generation_outcome_is_immutable_and_reports_success() -> None:
    deck = Deck(name="Biology")
    outcome = GenerationOutcome(
        decks=(deck,),
        discovered_sources=2,
        completed_sources=1,
        skipped_sources=1,
        failed_sources=(),
    )

    assert outcome.succeeded is True
    assert outcome.decks == (deck,)
    with pytest.raises(FrozenInstanceError):
        outcome.completed_sources = 2  # type: ignore[misc]


def test_generation_outcome_reports_source_failures() -> None:
    failure = SourceFailure(source=Path("bad.pdf"), reason="invalid PDF")
    outcome = GenerationOutcome(
        decks=(),
        discovered_sources=1,
        completed_sources=0,
        skipped_sources=0,
        failed_sources=(failure,),
    )

    assert outcome.succeeded is False
    assert outcome.failed_sources == (failure,)


def test_workflow_dtos_are_typed_and_immutable(tmp_path: Path) -> None:
    generation = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path / "output",
    )
    anki = AnkiExportOptions(
        deck_name="Study::Biology",
        api_key="ephemeral",
    )
    request = GenerateWorkflowRequest(
        generation_request=generation,
        language="pt_BR",
        check_auth=False,
        anki=anki,
    )

    assert request.generation_request is generation
    assert request.anki is anki
    assert anki.url == "http://127.0.0.1:8765"
    with pytest.raises(FrozenInstanceError):
        request.language = "en"  # type: ignore[misc]


def test_other_workflow_outcomes_expose_machine_consumable_values(
    tmp_path: Path,
) -> None:
    auth = AuthStatus(authenticated=True)
    cleanup_request = CleanupRequest(days=7, check_auth=False)
    cleanup = CleanupOutcome(deleted=3, failed=1)
    merge = MergeOutcome(
        output_path=tmp_path / "merged.csv",
        rows_written=20,
    )

    assert auth.authenticated is True
    assert cleanup_request.days == 7
    assert cleanup.succeeded is False
    assert merge.rows_written == 20


def test_cleanup_request_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError, match="positive"):
        CleanupRequest(days=0)
