"""Focused tests for the UI-independent workflow facade."""

import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, call, patch

import pytest

from flashcards_generator.delivery.composition import (
    UseCaseGenerationWorkflow,
    create_workflows,
)
from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.domain_models.exceptions import (
    LanguageConfigurationError,
    OperationCancelled,
)
from flashcards_generator.integrations.notebooklm.gateway import (
    NotebookLMAdapter,
)
from flashcards_generator.integrations.notebooklm.management import (
    NotebookLMManagement,
)
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    NullProgressReporter,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.dto.merge_request import MergeCsvRequest
from flashcards_generator.services.dto.workflow import (
    AnkiExportOptions,
    AuthStatus,
    CleanupOutcome,
    CleanupRequest,
)
from flashcards_generator.services.workflows import ApplicationWorkflows

pytestmark = pytest.mark.usefixtures("mock_bounded_process_output")


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
    def __init__(
        self,
        authenticated: bool = True,
        language_result: bool = True,
    ) -> None:
        self.authenticated = authenticated
        self.language_result = language_result
        self.cleanup_days: list[int | None] = []
        self.language: str | None = None
        self.language_calls: list[str] = []
        self.cancelled = False

    def auth_status(self) -> AuthStatus:
        return AuthStatus(self.authenticated, "status")

    def login(self) -> AuthStatus:
        self.authenticated = True
        return AuthStatus(True, "authenticated")

    def set_language(self, language: str) -> bool:
        self.language = language
        self.language_calls.append(language)
        return self.language_result

    def cleanup(
        self, *, days: int | None, check_auth: bool = False
    ) -> CleanupOutcome:
        if check_auth and not self.authenticated:
            raise PermissionError("NotebookLM authentication is required")
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
    notebooklm: FakeNotebookLM | NotebookLMManagement | None = None,
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


def test_generate_sets_the_requested_language_once(tmp_path: Path) -> None:
    notebooklm = FakeNotebookLM()
    generation = FakeGeneration(
        GenerationOutcome(
            decks=(),
            discovered_sources=0,
            completed_sources=0,
            skipped_sources=0,
            failed_sources=(),
        )
    )
    facade = _facade(generation, notebooklm)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path / "output",
        language="en_US",
    )
    reporter = NullProgressReporter()
    token = CancellationToken()

    outcome = facade.generate(request, reporter, token)

    assert outcome is generation.outcome
    assert notebooklm.language_calls == ["en_US"]
    assert generation.call == (request, reporter, token)


def test_generate_stops_when_language_configuration_fails(
    tmp_path: Path,
) -> None:
    notebooklm = FakeNotebookLM(language_result=False)
    generation = FakeGeneration(
        GenerationOutcome(
            decks=(),
            discovered_sources=0,
            completed_sources=0,
            skipped_sources=0,
            failed_sources=(),
        )
    )
    facade = _facade(generation, notebooklm)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path / "output",
        language="en_US",
    )
    reporter = RecordingReporter()

    with pytest.raises(
        LanguageConfigurationError,
        match=("^Unable to set NotebookLM output language: en_US$"),
    ) as error:
        facade.generate(request, reporter, CancellationToken())

    assert error.value.language == "en_US"
    assert notebooklm.language_calls == ["en_US"]
    assert generation.call is None
    assert reporter.events == []
    assert not request.output_dir.exists()


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


def test_composition_reexports_application_generation_workflow() -> None:
    from flashcards_generator.services.generation_workflow import (
        UseCaseGenerationWorkflow as ApplicationGenerationWorkflow,
    )

    assert UseCaseGenerationWorkflow is ApplicationGenerationWorkflow


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


def test_merge_requires_a_configured_operation(tmp_path: Path) -> None:
    request = MergeCsvRequest(folder_path=tmp_path)

    with pytest.raises(RuntimeError, match="^CSV merge is not configured$"):
        _facade().merge(request)


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


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, True), (2, False), (None, False)],
)
def test_notebooklm_management_maps_language_command_status(
    returncode: int | None,
    expected: bool,
) -> None:
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )
    result: subprocess.CompletedProcess[str] | None = (
        None
        if returncode is None
        else subprocess.CompletedProcess(["notebooklm"], returncode, "", "")
    )

    with patch.object(manager, "_run", return_value=result) as run:
        assert manager.set_language("pt_BR") is expected

    run.assert_called_once_with(["language", "set", "pt_BR"], timeout=60)


def test_notebooklm_management_maps_unavailable_and_failed_auth_commands() -> (
    None
):
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )
    results: list[subprocess.CompletedProcess[str] | None] = [
        None,
        subprocess.CompletedProcess(["auth"], 2, "", " denied "),
        subprocess.CompletedProcess(["login"], 2, "", ""),
    ]

    with patch.object(manager, "_run", side_effect=results):
        assert manager.auth_status() == AuthStatus(
            False, "unable to check authentication"
        )
        assert manager.auth_status() == AuthStatus(False, "denied")
        assert manager.login() == AuthStatus(False, "login failed")


def test_notebooklm_management_handles_missing_login_and_blank_language() -> (
    None
):
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )

    with patch.object(manager, "_run", return_value=None):
        assert manager.login() == AuthStatus(False, "unable to start login")

    with pytest.raises(ValueError, match="language must not be empty"):
        manager.set_language(" \t ")


def test_notebooklm_management_cleanup_passes_scope_and_auth_requirements() -> (
    None
):
    adapter = MagicMock(spec=NotebookLMAdapter)
    adapter.delete_all_notebooks.return_value = (2, 1)
    manager = NotebookLMManagement(
        "notebooklm",
        lambda _timeout: adapter,
        cleanup_show_progress=True,
    )

    assert manager.cleanup(days=None) == CleanupOutcome(deleted=2, failed=1)
    assert manager.cleanup(days=7) == CleanupOutcome(deleted=2, failed=1)
    adapter.delete_all_notebooks.assert_has_calls([
        call(show_progress=True),
        call(days=7, show_progress=True),
    ])

    with (
        patch.object(
            manager,
            "_run",
            return_value=subprocess.CompletedProcess(["auth"], 1, "", ""),
        ),
        pytest.raises(PermissionError, match="authentication is required"),
    ):
        manager.cleanup(days=None, check_auth=True)


def test_management_run_uses_profile_home_and_reaps_process_output(
    tmp_path: Path,
) -> None:
    home = tmp_path / "notebooklm-home"
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
        notebooklm_profile="study",
        notebooklm_home=home,
    )
    process = MagicMock()
    process.communicate.return_value = ("authenticated", "")
    process.returncode = 0

    with patch(
        "flashcards_generator.integrations.notebooklm.management.subprocess.Popen",
        return_value=process,
    ) as popen:
        assert manager.auth_status() == AuthStatus(True, "authenticated")

    assert popen.call_args.args[0] == [
        "notebooklm",
        "--profile",
        "study",
        "auth",
        "check",
    ]
    assert popen.call_args.kwargs["env"]["NOTEBOOKLM_HOME"] == str(home)
    assert popen.call_args.kwargs["start_new_session"] is True
    process.communicate.assert_called_once_with(timeout=10)


def test_management_run_translates_process_start_and_communication_failures() -> (
    None
):
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )
    with patch(
        "flashcards_generator.integrations.notebooklm.management.subprocess.Popen",
        side_effect=FileNotFoundError,
    ):
        assert manager.auth_status() == AuthStatus(
            False, "unable to check authentication"
        )

    process = MagicMock()
    process.communicate.side_effect = subprocess.TimeoutExpired(
        "notebooklm", 10
    )
    process.poll.return_value = 0
    with patch(
        "flashcards_generator.integrations.notebooklm.management.subprocess.Popen",
        return_value=process,
    ):
        assert manager.auth_status() == AuthStatus(
            False, "unable to check authentication"
        )

    process.wait.assert_called_once_with(timeout=5)


def test_management_run_rejects_calls_without_an_active_operation() -> None:
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )

    with pytest.raises(RuntimeError, match="operation is not active"):
        manager._run(["auth", "check"], timeout=10)


def test_management_run_returns_none_after_active_cancellation() -> None:
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )
    process = MagicMock()
    process.returncode = 0
    process.poll.return_value = 0

    def cancel_during_communication(
        *, timeout: float | None
    ) -> tuple[str, str]:
        manager.cancel_active()
        return "", ""

    process.communicate.side_effect = cancel_during_communication
    with (
        patch(
            "flashcards_generator.integrations.notebooklm.management.subprocess.Popen",
            return_value=process,
        ),
        manager._operation(),
    ):
        assert manager._run(["auth", "check"], timeout=10) is None


def test_stop_process_escalates_when_the_process_group_does_not_exit() -> None:
    process = MagicMock()
    process.pid = 4321
    process.poll.return_value = None
    process.wait.side_effect = [
        subprocess.TimeoutExpired("notebooklm", 5),
        None,
    ]

    with patch(
        "flashcards_generator.integrations.notebooklm.management.os.killpg"
    ) as killpg:
        NotebookLMManagement._stop_process(process)

    assert [call.args for call in killpg.call_args_list] == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.wait.call_count == 2


def test_signal_process_ignores_a_disappeared_process_leader() -> None:
    process = MagicMock()
    process.terminate.side_effect = ProcessLookupError

    with patch(
        "flashcards_generator.integrations.notebooklm.management.os.killpg",
        side_effect=PermissionError,
    ):
        NotebookLMManagement._signal_process(
            process, signal.SIGTERM, process.terminate
        )

    process.terminate.assert_called_once_with()


def test_create_workflows_wires_default_factories_and_adapters(
    tmp_path: Path,
) -> None:
    deck = Deck(name="Biology", flashcards=[Flashcard(front="Q", back="A")])
    use_case = FakeUseCase([deck], output_name="biology.csv")
    adapter = NotebookLMAdapter("notebooklm")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with (
        patch(
            "flashcards_generator.delivery.composition.find_notebooklm",
            return_value="notebooklm",
        ),
        patch(
            "flashcards_generator.delivery.composition.NotebookLMAdapter",
            return_value=adapter,
        ) as create_adapter,
        patch(
            "flashcards_generator.delivery.composition.GenerateFlashcardsUseCase",
            return_value=use_case,
        ) as create_use_case,
    ):
        workflows = create_workflows(
            notebooklm_profile="study",
            notebooklm_home=tmp_path / "profile",
        )
        request = GenerateFlashcardsRequest(
            input_dir=tmp_path,
            output_dir=output_dir,
            language="",
            timeout=75,
        )
        outcome = workflows.generate(
            request, RecordingReporter(), CancellationToken()
        )

    assert outcome.decks == (deck,)
    create_adapter.assert_called_once_with(
        "notebooklm",
        timeout=75,
        profile="study",
        notebooklm_home=tmp_path / "profile",
    )
    assert create_use_case.call_args.kwargs["generator"] is adapter

    source_dir = tmp_path / "merge"
    source_dir.mkdir()
    (source_dir / "source.csv").write_text('"Q","A"\n', encoding="utf-8")
    merged = workflows.merge(MergeCsvRequest(folder_path=source_dir))
    assert merged.rows_written == 1
    assert (source_dir / "merged_flashcards.csv").is_file()

    exporter = FakeAnkiExporter()
    with patch(
        "flashcards_generator.delivery.composition.AnkiConnectAdapter",
        return_value=exporter,
    ):
        assert (
            workflows.export_to_anki(
                [deck], AnkiExportOptions(deck_name="Study")
            )
            == 1
        )
    assert exporter.decks == [deck]


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


def test_cleanup_cancelled_during_adapter_construction_does_not_delete() -> (
    None
):
    factory_entered = Event()
    release_factory = Event()
    destructive_cleanup_started = Event()

    class RecordingAdapter(NotebookLMAdapter):
        def delete_all_notebooks(
            self, days: int | None = None, show_progress: bool = False
        ) -> tuple[int, int]:
            destructive_cleanup_started.set()
            return 1, 0

    adapters = [
        RecordingAdapter("notebooklm"),
        RecordingAdapter("notebooklm"),
    ]

    def create_adapter(timeout: int) -> NotebookLMAdapter:
        factory_entered.set()
        release_factory.wait(1)
        return adapters.pop(0)

    manager = NotebookLMManagement("notebooklm", create_adapter)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_cleanup = executor.submit(manager.cleanup, days=None)
        assert factory_entered.wait(1)

        manager.cancel_active()
        release_factory.set()

        with pytest.raises(OperationCancelled):
            first_cleanup.result(timeout=1)

    assert not destructive_cleanup_started.is_set()

    destructive_cleanup_started.clear()
    outcome = manager.cleanup(days=None)

    assert outcome == CleanupOutcome(deleted=1, failed=0)
    assert destructive_cleanup_started.is_set()


def test_cleanup_cancellation_during_auth_does_not_construct_adapter() -> None:
    auth_started = Event()
    release_auth = Event()
    adapter_constructed = Event()

    def create_adapter(timeout: int) -> NotebookLMAdapter:
        adapter_constructed.set()
        return NotebookLMAdapter("notebooklm", timeout=timeout)

    manager = NotebookLMManagement("notebooklm", create_adapter)
    facade = _facade(notebooklm=manager)

    def run_auth(
        arguments: list[str], *, timeout: float | None
    ) -> subprocess.CompletedProcess[str]:
        auth_started.set()
        if not release_auth.wait(1):
            raise TimeoutError("authentication was not released")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with (
        patch.object(manager, "_run", side_effect=run_auth),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        cleanup = executor.submit(facade.cleanup_all, confirmed=True)
        assert auth_started.wait(1)

        facade.cancel_management()
        release_auth.set()

        with pytest.raises(OperationCancelled):
            cleanup.result(timeout=1)

    assert not adapter_constructed.is_set()


def test_concurrent_management_operation_does_not_replace_first_cancellation() -> (
    None
):
    factory_entered = Event()
    release_factory = Event()
    destructive_cleanup_started = Event()

    class RecordingAdapter(NotebookLMAdapter):
        def delete_all_notebooks(
            self, days: int | None = None, show_progress: bool = False
        ) -> tuple[int, int]:
            destructive_cleanup_started.set()
            return 1, 0

    def create_adapter(timeout: int) -> NotebookLMAdapter:
        factory_entered.set()
        release_factory.wait(1)
        return RecordingAdapter("notebooklm", timeout=timeout)

    manager = NotebookLMManagement("/bin/true", create_adapter)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_cleanup = executor.submit(manager.cleanup, days=None)
        assert factory_entered.wait(1)

        with pytest.raises(RuntimeError, match="already active"):
            manager.auth_status()

        manager.cancel_active()
        release_factory.set()

        with pytest.raises(OperationCancelled):
            first_cleanup.result(timeout=1)

    assert not destructive_cleanup_started.is_set()

    assert manager.cleanup(days=None) == CleanupOutcome(deleted=1, failed=0)
    assert destructive_cleanup_started.is_set()


def test_cleanup_cancellation_after_adapter_registration_stops_adapter() -> (
    None
):
    cleanup_started = Event()
    adapter_stopped = Event()

    class BlockingAdapter(NotebookLMAdapter):
        def delete_all_notebooks(
            self, days: int | None = None, show_progress: bool = False
        ) -> tuple[int, int]:
            cleanup_started.set()
            if not adapter_stopped.wait(1):
                raise TimeoutError("adapter was not stopped")
            return 0, 1

        def cancel_active(self) -> None:
            adapter_stopped.set()

    manager = NotebookLMManagement(
        "notebooklm", lambda timeout: BlockingAdapter("notebooklm")
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        cleanup = executor.submit(manager.cleanup, days=None)
        assert cleanup_started.wait(1)

        manager.cancel_active()

        assert cleanup.result(timeout=1) == CleanupOutcome(deleted=0, failed=1)

    assert adapter_stopped.is_set()


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


def test_anki_export_requires_a_configured_exporter() -> None:
    deck = Deck(name="Study", flashcards=[Flashcard(front="Q", back="A")])
    cards_before = list(deck.flashcards)

    with pytest.raises(RuntimeError, match="^Anki export is not configured$"):
        _facade().export_to_anki(
            [deck],
            AnkiExportOptions(deck_name="Study"),
        )

    assert deck.flashcards == cards_before
