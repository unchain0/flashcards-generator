"""Persisted TUI settings controls."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Label, Static

from flashcards_generator.infrastructure.settings import Settings
from flashcards_generator.interfaces.tui.contracts import WorkflowServices
from flashcards_generator.interfaces.tui.widgets.shortcut_input import (
    ShortcutInput as Input,
)


class SettingsPanel(Vertical):
    """Edit and persist defaults through the shared settings boundary."""

    def __init__(
        self,
        services: WorkflowServices | None = None,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="workflow-panel")
        self.services = services
        self._settings = Settings()

    def compose(self) -> ComposeResult:
        yield Label("Settings", classes="workflow-title")
        with Horizontal(classes="action-row"):
            yield Checkbox("Resume completed chunks", id="settings-resume")
            yield Button(
                "Save settings",
                id="settings-save",
                variant="primary",
            )
        yield Label("Input directory", classes="field-label")
        yield Input(".", id="settings-input-dir")
        yield Label("Output directory", classes="field-label")
        yield Input("output", id="settings-output-dir")
        yield Label("Generation language", classes="field-label")
        yield Input("pt_BR", id="settings-language")
        yield Label("Difficulty", classes="field-label")
        yield Input("medium", id="settings-difficulty")
        yield Label("Quantity", classes="field-label")
        yield Input("standard", id="settings-quantity")
        yield Label("Instructions", classes="field-label")
        yield Input("", id="settings-instructions")
        yield Label("Include pattern", classes="field-label")
        yield Input("", id="settings-include")
        yield Label("Exclude pattern", classes="field-label")
        yield Input("", id="settings-exclude")
        yield Label("Timeout seconds", classes="field-label")
        yield Input("900", id="settings-timeout")
        yield Static("", id="settings-status")

    def on_mount(self) -> None:
        """Load persisted values once the form is mounted."""
        if self.services is None or not hasattr(self.services, "load"):
            return
        self._settings = self.services.load()
        values = {
            "#settings-input-dir": self._settings.input_dir,
            "#settings-output-dir": self._settings.output_dir,
            "#settings-language": self._settings.language,
            "#settings-difficulty": self._settings.difficulty,
            "#settings-quantity": self._settings.quantity,
            "#settings-instructions": self._settings.instructions,
            "#settings-include": self._settings.include_pattern or "",
            "#settings-exclude": self._settings.exclude_pattern or "",
            "#settings-timeout": self._settings.timeout,
        }
        for selector, value in values.items():
            self.query_one(selector, Input).value = str(value)
        self.query_one(
            "#settings-resume", Checkbox
        ).value = self._settings.resume

    def _read_settings(self) -> Settings:
        """Parse the current form values into persisted settings."""
        return Settings(
            input_dir=Path(self.query_one("#settings-input-dir", Input).value),
            output_dir=Path(
                self.query_one("#settings-output-dir", Input).value
            ),
            language=self.query_one("#settings-language", Input).value,
            difficulty=self.query_one("#settings-difficulty", Input).value,
            quantity=self.query_one("#settings-quantity", Input).value,
            instructions=self.query_one("#settings-instructions", Input).value,
            include_pattern=self.query_one("#settings-include", Input).value
            or None,
            exclude_pattern=self.query_one("#settings-exclude", Input).value
            or None,
            timeout=int(self.query_one("#settings-timeout", Input).value),
            resume=self.query_one("#settings-resume", Checkbox).value,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Validate and save the visible settings values."""
        if event.button.id != "settings-save":
            return
        if self.services is None or not hasattr(self.services, "save"):
            return
        try:
            settings = self._read_settings()
        except (TypeError, ValueError) as error:
            self.query_one("#settings-status", Static).update(
                f"Invalid settings: {error}"
            )
            return
        self.services.save(settings)
        self._settings = settings
        self.query_one("#settings-status", Static).update("Settings saved")
