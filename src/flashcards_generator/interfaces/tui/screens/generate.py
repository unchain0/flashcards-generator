"""Generation and progress workflow panels."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import ClassVar, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Label,
    ProgressBar,
    RichLog,
    SelectionList,
    Static,
)
from textual.worker import Worker, WorkerState

from flashcards_generator.application.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressReporter,
)
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.interfaces.tui.contracts import WorkflowServices
from flashcards_generator.interfaces.tui.screens.generation_validation import (
    GenerationValidationScreen,
)
from flashcards_generator.interfaces.tui.widgets.shortcut_input import (
    ShortcutInput as Input,
)
from flashcards_generator.interfaces.tui.widgets.source_picker import (
    SourcePicker,
)

GenerationComplete = Callable[[GenerationOutcome], None]
SUPPORTED_SOURCE_SUFFIXES: frozenset[str] = frozenset({".pdf", ".pptx"})


class ProgressPanel(Vertical):
    """Presentation-only progress surface with cooperative cancellation."""

    def __init__(
        self,
        *,
        on_cancel: Callable[[], None] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        yield Label("Progress", classes="section-title")
        yield Static("Ready", id="progress-status")
        yield Static(
            "File: - | Stage: - | Cards: 0 | Elapsed: 0s",
            id="progress-detail",
        )
        yield ProgressBar(total=1, show_eta=False, id="progress-bar")
        yield RichLog(id="progress-log", wrap=True, markup=False)
        yield Button(
            "Cancel",
            id="progress-cancel",
            variant="error",
            disabled=True,
        )

    def show_event(self, event: ProgressEvent) -> None:
        """Render a machine-independent application progress event."""
        detail = self._event_detail(event)
        self.query_one("#progress-status", Static).update(detail)
        self.query_one("#progress-detail", Static).update(
            self._event_progress_detail(event)
        )
        self._update_progress_bar(event)
        self.query_one("#progress-log", RichLog).write(detail)

    @staticmethod
    def _event_detail(event: ProgressEvent) -> str:
        detail = event.message
        if event.current is not None and event.total is not None:
            return f"{detail} ({event.current}/{event.total})"
        return detail

    def _event_progress_detail(self, event: ProgressEvent) -> str:
        file_name = event.source.name if event.source is not None else "-"
        cards = event.cards if event.cards is not None else 0
        return (
            f"File: {file_name} | Stage: {event.stage.value} | "
            f"Chunk: {event.chunk_index or '-'} | Cards: {cards} | "
            f"Elapsed: {self._elapsed_seconds()}s"
        )

    def _update_progress_bar(self, event: ProgressEvent) -> None:
        if event.total is not None and event.total > 0:
            progress = self.query_one("#progress-bar", ProgressBar)
            progress.total = event.total
            progress.update(progress=min(event.current or 0, event.total))

    def show_status(self, message: str) -> None:
        """Render a terminal workflow status."""
        self.query_one("#progress-status", Static).update(message)

    def set_running(self, running: bool) -> None:
        """Enable cancellation only while an operation is active."""
        self.query_one("#progress-cancel", Button).disabled = not running

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle cancellation at the same widget as its button."""
        if event.button.id == "progress-cancel" and self._on_cancel:
            self._on_cancel()

    def begin(self) -> None:
        """Start elapsed-time tracking for a new operation."""
        self._started_at = monotonic()

    def _elapsed_seconds(self) -> int:
        started_at = getattr(self, "_started_at", None)
        return 0 if started_at is None else int(monotonic() - started_at)


class _UiProgressReporter(ProgressReporter):
    def __init__(self, panel: ProgressPanel) -> None:
        self._panel = panel

    def publish(self, event: ProgressEvent) -> None:
        self._panel.app.call_from_thread(self._panel.show_event, event)


class GeneratePanel(Vertical):
    """Collect generation inputs and delegate execution to a service."""

    DEFAULT_CLASSES: ClassVar[str] = "workflow-panel"

    def __init__(
        self,
        services: WorkflowServices | None = None,
        *,
        on_complete: GenerationComplete | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.services = services
        self.on_complete = on_complete
        self._token: CancellationToken | None = None
        self._worker: Worker[GenerationOutcome] | None = None
        self.generation_worker_count = 0

    def compose(self) -> ComposeResult:
        yield Label("Generate flashcards", classes="workflow-title")
        with Horizontal(classes="action-row"):
            yield Button("Refresh sources", id="generate-refresh")
            yield Button("Select all", id="generate-select-all")
            yield Button("Start", id="generate-start", variant="primary")
            yield Checkbox("No wait", id="generate-no-wait")
            yield Checkbox(
                "Resume completed chunks",
                id="generate-resume",
                value=True,
            )
        yield ProgressPanel(
            on_cancel=self.cancel_generation,
            id="progress-panel",
        )
        yield Label("Input directory", classes="field-label")
        yield SourcePicker(input_id="generate-input-dir", id="source-picker")
        yield Label("Output directory", classes="field-label")
        yield Input("output", id="generate-output-dir")
        yield Label("Difficulty", classes="field-label")
        yield Input("medium", id="generate-difficulty")
        yield Label("Quantity", classes="field-label")
        yield Input("standard", id="generate-quantity")
        yield Label("Language", classes="field-label")
        yield Input("pt_BR", id="generate-language")
        yield Label("Instructions", classes="field-label")
        yield Input("", id="generate-instructions")
        yield Label("Include pattern", classes="field-label")
        yield Input("", id="generate-include")
        yield Label("Exclude pattern", classes="field-label")
        yield Input("", id="generate-exclude")
        yield Label("Explicit files (comma-separated)", classes="field-label")
        yield Input("", id="generate-files")
        yield Label("Timeout seconds", classes="field-label")
        yield Input("900", id="generate-timeout")
        yield SelectionList[str](id="generate-sources")

    def on_mount(self) -> None:
        """Load persisted defaults into the generation form."""
        self._progress.add_class("is-hidden")
        if self.services is None or not hasattr(self.services, "load"):
            return
        settings = self.services.load()
        values = {
            "#generate-input-dir": settings.input_dir,
            "#generate-output-dir": settings.output_dir,
            "#generate-difficulty": settings.difficulty,
            "#generate-quantity": settings.quantity,
            "#generate-language": settings.language,
            "#generate-instructions": settings.instructions,
            "#generate-include": settings.include_pattern,
            "#generate-exclude": settings.exclude_pattern,
            "#generate-timeout": settings.timeout,
        }
        for selector, value in values.items():
            self.query_one(selector, Input).value = (
                "" if value is None else str(value)
            )
        self.query_one("#generate-resume", Checkbox).value = settings.resume

    @property
    def active_generation_worker(self) -> bool:
        """Return whether this panel owns a running generation worker."""
        return self._worker is not None and not self._worker.is_finished

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route controls without embedding generation business logic."""
        button_id = event.button.id
        if button_id == "generate-refresh":
            self.refresh_sources()
        elif button_id == "generate-select-all":
            self.query_one("#generate-sources", SelectionList).select_all()
        elif button_id == "generate-start":
            self.start_generation()
        elif button_id == "progress-cancel":
            self.cancel_generation()

    def on_source_picker_selected(self, event: SourcePicker.Selected) -> None:
        """Refresh files after directory navigation in the picker."""
        self.query_one("#generate-input-dir", Input).value = str(event.path)
        self.refresh_sources()

    def refresh_sources(self) -> None:
        """Discover supported files for presentation and explicit selection."""
        source_list = self.query_one("#generate-sources", SelectionList)
        source_list.clear_options()
        root = Path(self.query_one("#generate-input-dir", Input).value)
        if not root.is_dir():
            self._progress.show_status("Input directory not found")
            return
        paths = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.suffix.casefold() in SUPPORTED_SOURCE_SUFFIXES
            ),
            key=lambda path: str(path).casefold(),
        )
        source_list.add_options(
            (str(path.relative_to(root)), str(path), False) for path in paths
        )
        self._progress.show_status(f"{len(paths)} source(s) found")

    def start_generation(self) -> None:
        """Build the application DTO and execute the injected service once."""
        if self.services is None or self.active_generation_worker:
            return
        try:
            request = self._request_from_form()
        except (TypeError, ValueError) as error:
            message = f"Invalid generation options: {error}"
            self._progress.show_status(message)
            self.app.push_screen(GenerationValidationScreen(str(error)))
            return
        self._token = CancellationToken()
        self._progress.begin()
        self._progress.remove_class("is-hidden")
        self._progress.scroll_visible()
        self._progress.set_running(True)
        self._progress.show_status("Starting generation")
        self.generation_worker_count += 1
        self._worker = self.run_worker(
            lambda: self.services.generate(
                request,
                _UiProgressReporter(self._progress),
                self._token,
            ),
            name="generation",
            group="generation",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _request_from_form(self) -> GenerateFlashcardsRequest:
        """Translate visible form values into the real generation DTO."""
        root = Path(self.query_one("#generate-input-dir", Input).value)
        output = Path(self.query_one("#generate-output-dir", Input).value)
        if not str(root) or not str(output):
            raise ValueError("input and output directories are required")
        difficulty = self._validated_difficulty()
        quantity = self._validated_quantity()
        return GenerateFlashcardsRequest(
            input_dir=root,
            output_dir=output,
            difficulty=difficulty,
            quantity=quantity,
            language=self.query_one("#generate-language", Input).value,
            instructions=self.query_one("#generate-instructions", Input).value,
            wait_for_completion=not self.query_one(
                "#generate-no-wait", Checkbox
            ).value,
            timeout=self._validated_timeout(),
            resume=self.query_one("#generate-resume", Checkbox).value,
            include_pattern=self.query_one("#generate-include", Input).value
            or None,
            exclude_pattern=self.query_one("#generate-exclude", Input).value
            or None,
            explicit_files=self._explicit_files(),
        )

    def _validated_difficulty(self) -> str:
        difficulty = self.query_one("#generate-difficulty", Input).value
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("difficulty must be easy, medium, or hard")
        return difficulty

    def _validated_quantity(self) -> str:
        quantity = self.query_one("#generate-quantity", Input).value
        if quantity not in {"fewer", "standard", "more"}:
            raise ValueError("quantity must be fewer, standard, or more")
        return quantity

    def _validated_timeout(self) -> int:
        timeout = int(self.query_one("#generate-timeout", Input).value)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return timeout

    def _explicit_files(self) -> list[str]:
        selected = list(
            self.query_one("#generate-sources", SelectionList).selected
        )
        explicit_text = self.query_one("#generate-files", Input).value
        return selected or [
            item.strip() for item in explicit_text.split(",") if item.strip()
        ]

    def cancel_generation(self) -> None:
        """Request cooperative cancellation from the active service call."""
        if self._token is not None:
            self._progress.show_status("Cancelling generation")
            self._token.cancel()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Render completion after the thread reaches a terminal state."""
        if event.worker is not self._worker:
            return
        if event.state is WorkerState.SUCCESS:
            self._show_generation_success(event.worker)
        elif event.state in {WorkerState.CANCELLED, WorkerState.ERROR}:
            self._show_generation_stopped()

    def _show_generation_success(
        self, worker: Worker[GenerationOutcome]
    ) -> None:
        outcome = cast(GenerationOutcome, worker.result)
        self._progress.set_running(False)
        self._progress.show_status(
            f"Complete: {outcome.completed_sources} source(s)"
        )
        if self.on_complete is not None:
            self.on_complete(outcome)

    def _show_generation_stopped(self) -> None:
        self._progress.set_running(False)
        message = (
            "Generation cancelled"
            if self._token is not None and self._token.is_cancelled
            else "Generation failed"
        )
        self._progress.show_status(message)

    @property
    def _progress(self) -> ProgressPanel:
        return self.query_one("#progress-panel", ProgressPanel)
