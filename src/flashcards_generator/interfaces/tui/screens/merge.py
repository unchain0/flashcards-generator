"""CSV merge workflow panel."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Label, Static
from textual.worker import Worker, WorkerState

from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.application.dto.workflow import MergeOutcome
from flashcards_generator.interfaces.tui.contracts import WorkflowServices
from flashcards_generator.interfaces.tui.widgets.shortcut_input import (
    ShortcutInput,
)

MergeComplete = Callable[[MergeOutcome], None]


class MergePanel(Vertical):
    """Collect merge options and delegate CSV processing to a service."""

    def __init__(
        self,
        services: WorkflowServices | None = None,
        *,
        on_complete: MergeComplete | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="workflow-panel")
        self.services = services
        self.on_complete = on_complete
        self._worker: Worker[MergeOutcome] | None = None

    def compose(self) -> ComposeResult:
        yield Label("Merge CSV files", classes="workflow-title")
        yield Label("CSV folder", classes="field-label")
        yield ShortcutInput(".", id="merge-folder")
        yield Label("Output filename", classes="field-label")
        yield ShortcutInput("merged_flashcards.csv", id="merge-output")
        with Horizontal(classes="action-row"):
            yield Checkbox("Remove duplicate cards", id="merge-deduplicate")
            yield Checkbox(
                "Search subfolders", id="merge-recursive", value=True
            )
            yield Button("Merge", id="merge-start", variant="primary")
        yield Static("Ready", id="merge-count")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "merge-start" or self.services is None:
            return
        try:
            request = MergeCsvRequest(
                folder_path=Path(
                    self.query_one("#merge-folder", ShortcutInput).value
                ),
                output_filename=self.query_one(
                    "#merge-output", ShortcutInput
                ).value,
                deduplicate=self.query_one(
                    "#merge-deduplicate", Checkbox
                ).value,
                recursive=self.query_one("#merge-recursive", Checkbox).value,
            )
        except (TypeError, ValueError) as error:
            self.query_one("#merge-count", Static).update(
                f"Invalid merge options: {error}"
            )
            return
        self.query_one("#merge-count", Static).update("Merging")
        self._worker = self.run_worker(
            lambda: self.services.merge(request),
            name="merge",
            group="merge",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self._worker:
            return
        if event.state is WorkerState.SUCCESS:
            outcome = cast(MergeOutcome, event.worker.result)
            self.query_one("#merge-count", Static).update(
                f"{outcome.rows_before} row(s) before, "
                f"{outcome.rows_written} written, "
                f"{outcome.duplicates_removed} duplicate(s) removed"
            )
            if self.on_complete is not None:
                self.on_complete(outcome)
        elif event.state is WorkerState.ERROR:
            self.query_one("#merge-count", Static).update("Merge failed")
