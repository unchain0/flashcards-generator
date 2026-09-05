"""NotebookLM authentication and cleanup controls."""

from __future__ import annotations

from typing import Literal, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static
from textual.worker import Worker, WorkerState

from flashcards_generator.application.dto.workflow import (
    AuthStatus,
    CleanupOutcome,
)
from flashcards_generator.interfaces.tui.contracts import WorkflowServices
from flashcards_generator.interfaces.tui.widgets.shortcut_input import (
    ShortcutInput,
)

ManagementResult = AuthStatus | CleanupOutcome | bool
ManagementOperation = Literal["status", "login", "cleanup", "language"]


class CleanupConfirmation(Vertical):
    """Explicit destructive-action confirmation surface."""

    def compose(self) -> ComposeResult:
        yield Label(
            "Delete every NotebookLM notebook?", classes="section-title"
        )
        yield Static("This action cannot be undone.")
        with Horizontal(classes="action-row"):
            yield Button(
                "Delete all",
                id="confirm-cleanup-all",
                variant="error",
            )
            yield Button("Keep notebooks", id="cancel-cleanup-all")


class NotebookLMPanel(Vertical):
    """Delegate provider management through an injected service boundary."""

    def __init__(
        self,
        services: WorkflowServices | None = None,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="workflow-panel")
        self.services = services
        self._operation: ManagementOperation | None = None
        self._worker: Worker[ManagementResult] | None = None

    def compose(self) -> ComposeResult:
        yield Label("NotebookLM", classes="workflow-title")
        yield Static("Authentication not checked", id="notebooklm-auth-status")
        yield Label("Output language", classes="field-label")
        yield ShortcutInput("pt_BR", id="notebooklm-language")
        with Horizontal(classes="action-row"):
            yield Button("Refresh", id="notebooklm-refresh")
            yield Button("Login", id="notebooklm-login", variant="primary")
            yield Button("Set language", id="notebooklm-set-language")
            yield Button(
                "Cleanup all",
                id="notebooklm-cleanup-all",
                variant="error",
            )
        yield Vertical(id="notebooklm-confirmation-slot")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if await self._handle_confirmation_button(button_id):
            return
        operations: dict[str | None, ManagementOperation] = {
            "notebooklm-refresh": "status",
            "notebooklm-login": "login",
            "notebooklm-set-language": "language",
        }
        operation = operations.get(button_id)
        if operation is not None:
            self._run(operation)

    async def _handle_confirmation_button(self, button_id: str | None) -> bool:
        if button_id == "notebooklm-cleanup-all":
            await self._show_cleanup_confirmation()
            return True
        if button_id == "cancel-cleanup-all":
            await self._clear_confirmation()
            return True
        if button_id == "confirm-cleanup-all":
            await self._clear_confirmation()
            self._run("cleanup")
            return True
        return False

    def _run(self, operation: ManagementOperation) -> None:
        if self.services is None:
            return
        if self._worker is not None and not self._worker.is_finished:
            return
        self._operation = operation
        self.query_one("#notebooklm-auth-status", Static).update(
            "Checking" if operation == "status" else f"Running {operation}"
        )
        service = self.services

        def invoke() -> ManagementResult:
            if operation == "status":
                return service.auth_status()
            if operation == "login":
                return service.login()
            if operation == "language":
                return service.set_language(
                    self.query_one("#notebooklm-language", ShortcutInput).value
                )
            return service.cleanup_all(confirmed=True)

        self._worker = self.run_worker(
            invoke,
            name=f"notebooklm-{operation}",
            group="notebooklm",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self._worker:
            return
        status = self.query_one("#notebooklm-auth-status", Static)
        if event.state is WorkerState.ERROR:
            status.update(f"{self._operation or 'operation'} failed")
        elif event.state is WorkerState.SUCCESS:
            self._show_result(status, event.worker)

    def cancel_active(self) -> None:
        """Cancel this panel's worker and its provider subprocess."""
        if self._worker is not None and not self._worker.is_finished:
            self._worker.cancel()
        if self.services is not None:
            self.services.cancel_management()

    @staticmethod
    def _show_result(status: Static, worker: Worker[ManagementResult]) -> None:
        result = cast(ManagementResult, worker.result)
        if isinstance(result, AuthStatus):
            status.update(result.message)
        elif isinstance(result, CleanupOutcome):
            status.update(
                f"Cleanup complete: {result.deleted} deleted, "
                f"{result.failed} failed"
            )
        else:
            status.update(
                "Language saved" if result else "Language update failed"
            )

    async def _show_cleanup_confirmation(self) -> None:
        slot = self.query_one("#notebooklm-confirmation-slot", Vertical)
        if not slot.query("#cleanup-confirm"):
            await slot.mount(CleanupConfirmation(id="cleanup-confirm"))

    async def _clear_confirmation(self) -> None:
        slot = self.query_one("#notebooklm-confirmation-slot", Vertical)
        await slot.remove_children()
