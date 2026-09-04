"""Focused tests for the UI-independent workflow facade."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.application.contracts import (
    CancellationToken,
    GenerationOutcome,
    NullProgressReporter,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.application.dto.workflow import (
    AnkiExportOptions,
    AuthStatus,
    CleanupOutcome,
    CleanupRequest,
)
from flashcards_generator.application.workflows import ApplicationWorkflows
from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.infrastructure.settings import SettingsRepository
from flashcards_generator.interfaces.composition import (
    ApplicationServices,
    NotebookLMManagement,
    UseCaseGenerationWorkflow,
)


class FakeGeneration:
    def __init__(self, outcome: GenerationOutcome) -> None:
        self.outcome = outcome
        self.call: (
            tuple[
                GenerateFlashcardsRequest,
                ProgressReporter,
                CancellationToken,
            ]
            | None
        ) = None

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        self.call = (request, reporter, token)
        return self.outcome


class FakeNotebookLM:
    def __init__(self, authenticated: bool = True) -> None:
        self.authenticated = authenticated
        self.cleanup_days: list[int | None] = []
        self.language: str | None = None
        self.cancelled = False

    def auth_status(self) -> AuthStatus:
        return AuthStatus(self.authenticated, "status")

    def login(self) -> AuthStatus:
        self.authenticated = True
        return AuthStatus(True, "authenticated")

    def set_language(self, language: str) -> bool:
        self.language = language
        return True

    def cleanup(self, *, days: int | None) -> CleanupOutcome:
        self.cleanup_days.append(days)
        return CleanupOutcome(deleted=2, failed=0)

    def cancel_active(self) -> None:
        self.cancelled = True


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


class FakeUseCase:
    def __init__(
        self,
        decks: list[Deck],
        *,
        failed: bool = False,
        output_name: str | None = None,
    ) -> None:
        self.decks = decks
        self.last_run_had_errors = failed
        self.output_name = output_name
        self.request: GenerateFlashcardsRequest | None = None

    def execute(
        self,
        request: GenerateFlashcardsRequest,
        reporter,
        token,
    ) -> list[Deck]:
        self.request = request
        token.raise_if_cancelled()
        reporter.publish(
            ProgressEvent(
                stage=ProgressStage.DISCOVERY,
                state=ProgressState.COMPLETED,
                message="discovered",
                current=1,
                total=1,
            )
        )
        reporter.publish(
            ProgressEvent(
                stage=ProgressStage.SOURCE,
                state=ProgressState.COMPLETED,
                message="completed",
                source=request.input_dir / "biology.pdf",
            )
        )
        if self.output_name is not None:
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / self.output_name).write_text(
                "fresh", encoding="utf-8"
            )
        return self.decks


class FakeAnkiExporter:
    def __init__(self) -> None:
        self.decks: list[Deck] = []

    def export(self, deck: Deck) -> int:
        self.decks.append(deck)
        return deck.total_cards


def _facade(
    generation: FakeGeneration | None = None,
    notebooklm: FakeNotebookLM | None = None,
    **kwargs,
) -> ApplicationWorkflows:
    return ApplicationWorkflows(
        generation
        or FakeGeneration(
            GenerationOutcome(
                decks=(),
                discovered_sources=0,
                completed_sources=0,
                skipped_sources=0,
                failed_sources=(),
            )
        ),
        notebooklm or FakeNotebookLM(),
        **kwargs,
    )


def test_generate_preserves_duck_typed_api_and_outcome(tmp_path: Path) -> None:
    deck = Deck(name="Biology")
    expected = GenerationOutcome(
        decks=(deck,),
        discovered_sources=1,
        completed_sources=1,
        skipped_sources=0,
        failed_sources=(),
    )
    operation = FakeGeneration(expected)
    facade = _facade(operation)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path / "output",
    )
    reporter = NullProgressReporter()
    token = CancellationToken()

    result = facade.generate(request, reporter, token)

    assert result is expected
    assert operation.call == (request, reporter, token)
    assert result.decks[0].name == "Biology"


def test_composed_generation_adapter_returns_outcome_and_events(
    tmp_path: Path,
) -> None:
    deck = Deck(name="Biology")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "stale.csv").write_text("stale", encoding="utf-8")
    use_case = FakeUseCase([deck], output_name="fresh.csv")
    operation = UseCaseGenerationWorkflow(lambda timeout: use_case)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=output_dir,
    )
    reporter = RecordingReporter()

    outcome = operation.generate(request, reporter, CancellationToken())

    assert use_case.request is request
    assert outcome.decks == (deck,)
    assert outcome.completed_sources == 1
    assert outcome.csv_paths == (output_dir / "fresh.csv",)
    assert [event.state for event in reporter.events] == [
        ProgressState.COMPLETED,
        ProgressState.COMPLETED,
    ]


def test_merge_returns_machine_readable_path_and_count(tmp_path: Path) -> None:
    request = MergeCsvRequest(
        folder_path=tmp_path,
        output_filename="combined.csv",
    )
    calls: list[MergeCsvRequest] = []

    def merge(operation_request: MergeCsvRequest) -> int:
        calls.append(operation_request)
        return 12

    outcome = _facade(merge_operation=merge).merge(request)

    assert calls == [request]
    assert outcome.output_path == tmp_path / "combined.csv"
    assert outcome.rows_before == 12
    assert outcome.rows_written == 12
    assert outcome.duplicates_removed == 0


def test_auth_login_language_and_scoped_cleanup_delegate() -> None:
    notebooklm = FakeNotebookLM(authenticated=False)
    facade = _facade(notebooklm=notebooklm)

    status = facade.auth_status()
    logged_in = facade.login()
    language_set = facade.set_language("en")
    outcome = facade.cleanup(CleanupRequest(days=7))

    assert (
        status.authenticated,
        logged_in.authenticated,
        language_set,
        notebooklm.language,
        notebooklm.cleanup_days,
        outcome.deleted,
    ) == (False, True, True, "en", [7], 2)


def test_application_services_delegates_management_cancellation(
    tmp_path: Path,
) -> None:
    """Given composed services, cancellation reaches NotebookLM management."""
    notebooklm = FakeNotebookLM()
    services = ApplicationServices(
        _facade(notebooklm=notebooklm),
        SettingsRepository(tmp_path / "settings.json"),
    )

    services.cancel_management()

    assert notebooklm.cancelled is True


def test_login_does_not_follow_cancelled_successful_login() -> None:
    """Given cancellation during login, auth check is not launched."""
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )
    calls: list[list[str]] = []

    def run(
        arguments: list[str],
        *,
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        manager.cancel_active()
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with patch.object(manager, "_run", side_effect=run):
        status = manager.login()

    assert status == AuthStatus(False, "login cancelled")
    assert calls == [["login"]]


def test_cleanup_all_requires_explicit_confirmation_before_auth() -> None:
    notebooklm = FakeNotebookLM(authenticated=False)
    facade = _facade(notebooklm=notebooklm)

    with pytest.raises(ValueError, match="explicit confirmation"):
        facade.cleanup_all(confirmed=False)

    assert notebooklm.cleanup_days == []
    with pytest.raises(PermissionError, match="authentication"):
        facade.cleanup_all(confirmed=True)
    assert notebooklm.cleanup_days == []


def test_cleanup_all_delegates_only_when_confirmed() -> None:
    notebooklm = FakeNotebookLM()
    outcome = _facade(notebooklm=notebooklm).cleanup_all(confirmed=True)

    assert notebooklm.cleanup_days == [None]
    assert outcome == CleanupOutcome(deleted=2, failed=0)


def test_anki_export_uses_port_and_preserves_cards() -> None:
    exporter = FakeAnkiExporter()
    facade = _facade(anki_exporter_factory=lambda options: exporter)
    decks = [
        Deck(name="One", flashcards=[Flashcard(front="Q1", back="A1")]),
        Deck(name="Two", flashcards=[Flashcard(front="Q2", back="A2")]),
    ]

    imported = facade.export_to_anki(
        decks,
        AnkiExportOptions(deck_name="Study"),
    )

    assert imported == 2
    assert exporter.decks == decks
