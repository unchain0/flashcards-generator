"""Primary Textual application for the flashcards generator."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

from loguru import logger
from platformdirs import user_log_path
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from flashcards_generator.application.contracts import GenerationOutcome
from flashcards_generator.application.dto.workflow import MergeOutcome
from flashcards_generator.interfaces.composition import create_services
from flashcards_generator.interfaces.tui.contracts import WorkflowServices
from flashcards_generator.interfaces.tui.screens import (
    GeneratePanel,
    MergePanel,
    NotebookLMPanel,
    ResultsPanel,
    SettingsPanel,
)


class HelpScreen(ModalScreen[None]):
    """Show keyboard shortcuts without leaving the current workflow."""

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        Binding("escape", "close_help", "Close", priority=True),
        Binding("q", "quit_app", "Quit", priority=True),
    ]

    def compose(self) -> ComposeResult:
        """Render the shortcuts panel."""
        with Container(id="help-dialog"):
            yield Label("Keyboard shortcuts", id="help-title")
            yield Static(
                "q  Quit\n"
                "g  Generate\n"
                "r  Results\n"
                "m  Merge\n"
                "n  NotebookLM\n"
                "s  Settings\n"
                "Ctrl+R  Refresh sources\n"
                "?  Help\n"
                "Esc  Close",
                id="help-shortcuts",
            )

    def action_close_help(self) -> None:
        """Close the help panel."""
        self.dismiss(None)

    def action_quit_app(self) -> None:
        """Quit the primary app from the help surface."""
        self.app.exit()


class FlashcardsApp(App[None]):
    """Primary Textual shell with injectable application services."""

    TITLE = "Flashcards Generator"
    CSS_PATH = "styles.tcss"
    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        Binding("q", "quit", "Q", priority=True),
        Binding("g", "show_generate", "G", priority=True),
        Binding("r", "show_results", "R", priority=True),
        Binding("m", "show_merge", "M", priority=True),
        Binding("n", "show_notebooklm", "N", priority=True),
        Binding("s", "show_settings", "S", priority=True),
        Binding("ctrl+r", "refresh", "R", priority=True),
        Binding("escape", "cancel", "E", priority=True),
        Binding("?", "show_help", "H", priority=True),
    ]

    def __init__(
        self,
        services: WorkflowServices | None = None,
        *,
        show_help: bool = False,
    ) -> None:
        super().__init__()
        self.services = services or cast(WorkflowServices, create_services())
        self._show_help = show_help
        self._ui_log_sink: int | None = None
        self._file_log_sink: int | None = None

    def compose(self) -> ComposeResult:
        """Render all workflow panels through the shared service boundary."""
        yield Header(show_clock=True)
        with TabbedContent(id="main-tabs"):
            with TabPane("Generate", id="generate"):
                yield GeneratePanel(
                    self.services,
                    on_complete=self._show_generation_results,
                    id="generate-panel",
                )
            with TabPane("Results", id="results"):
                yield ResultsPanel(
                    on_merge=self.action_show_merge,
                    on_new_generation=self.action_show_generate,
                    id="results-panel",
                )
            with TabPane("Merge", id="merge"):
                yield MergePanel(
                    self.services,
                    on_complete=self._show_merge_result,
                    id="merge-panel",
                )
            with TabPane("NotebookLM", id="notebooklm"):
                yield NotebookLMPanel(self.services, id="notebooklm-panel")
            with TabPane("Settings", id="settings"):
                yield SettingsPanel(self.services, id="settings-panel")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        """Open help when a primary entrypoint explicitly requests it."""
        self._configure_logging()
        if self._show_help:
            self.push_screen(HelpScreen())

    def on_unmount(self) -> None:
        """Cancel active work and remove interface-owned log sinks."""
        panels = list(self.query(GeneratePanel))
        if panels and panels[0].active_generation_worker:
            panels[0].cancel_generation()
        management_panels = list(self.query(NotebookLMPanel))
        if management_panels:
            management_panels[0].cancel_active()
        if self._ui_log_sink is not None:
            logger.remove(self._ui_log_sink)
            self._ui_log_sink = None
        if self._file_log_sink is not None:
            logger.remove(self._file_log_sink)
            self._file_log_sink = None

    def _configure_logging(self) -> None:
        """Route application logs to the TUI and an XDG log file."""
        logger.remove()
        self._ui_log_sink = logger.add(
            self._route_log,
            level="INFO",
            format="{message}",
            enqueue=False,
        )
        try:
            log_path = Path(user_log_path("flashcards-generator"))
            log_path.mkdir(parents=True, exist_ok=True)
            self._file_log_sink = logger.add(
                str(log_path / "flashcards.log"),
                level="INFO",
                format="{time:YYYY-MM-DD HH:mm:ss} {level} {message}",
                rotation="1 MB",
                retention=5,
                enqueue=True,
                encoding="utf-8",
            )
        except OSError:
            self._file_log_sink = None

    def _route_log(self, message: object) -> None:
        """Forward one loguru message to the app thread."""
        try:
            self.call_from_thread(self._append_log, str(message).rstrip())
        except RuntimeError:
            return

    def _append_log(self, message: str) -> None:
        """Render a log line without writing directly to the terminal."""
        if self.is_running:
            self.query_one("#progress-log", RichLog).write(message)

    @property
    def generation_worker_count(self) -> int:
        """Expose the generation worker count for lifecycle verification."""
        return self.query_one(
            "#generate-panel", GeneratePanel
        ).generation_worker_count

    @property
    def active_generation_worker(self) -> bool:
        """Return whether a generation worker remains active."""
        return self.query_one(
            "#generate-panel", GeneratePanel
        ).active_generation_worker

    def action_show_generate(self) -> None:
        """Focus the Generate tab."""
        self._show_tab("generate")

    def action_show_results(self) -> None:
        """Focus the Results tab."""
        self._show_tab("results")

    def action_show_merge(self) -> None:
        """Focus the Merge tab."""
        self._show_tab("merge")

    def action_show_notebooklm(self) -> None:
        """Focus the NotebookLM tab."""
        self._show_tab("notebooklm")

    def action_show_settings(self) -> None:
        """Focus the Settings tab."""
        self._show_tab("settings")

    def action_refresh(self) -> None:
        """Refresh the source list without starting a workflow."""
        self.query_one("#generate-panel", GeneratePanel).refresh_sources()

    def action_cancel(self) -> None:
        """Cancel only the active generation workflow."""
        if isinstance(self.screen, ModalScreen):
            self.pop_screen()
            return
        panel = self.query_one("#generate-panel", GeneratePanel)
        if panel.active_generation_worker:
            panel.cancel_generation()

    def action_show_help(self) -> None:
        """Open keyboard shortcut help."""
        self.push_screen(HelpScreen())

    def _show_generation_results(self, outcome: GenerationOutcome) -> None:
        """Publish completed generation data to Results."""
        results = self.query_one("#results-panel", ResultsPanel)
        results.show_decks(outcome.decks)
        results.show_csv_paths(outcome.csv_paths)
        results.show_generation_summary(
            discovered=outcome.discovered_sources,
            completed=outcome.completed_sources,
            skipped=outcome.skipped_sources,
            failed=len(outcome.failed_sources),
            elapsed_seconds=outcome.elapsed_seconds,
        )
        self.action_show_results()

    def _show_merge_result(self, outcome: MergeOutcome) -> None:
        """Publish completed merge data to Results."""
        self.query_one("#results-panel", ResultsPanel).show_csv(
            outcome.output_path,
            outcome.rows_written,
            rows_before=outcome.rows_before,
            duplicates_removed=outcome.duplicates_removed,
        )
        self.action_show_results()

    def _show_tab(self, tab_id: str) -> None:
        """Activate a tab by its semantic identifier."""
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = tab_id
        self.set_focus(None)
