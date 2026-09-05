"""Persisted TUI settings controls."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.suggester import SuggestFromList
from textual.widgets import Button, Checkbox, Label, Select, Static

from flashcards_generator.infrastructure.settings import Settings
from flashcards_generator.interfaces.tui.contracts import WorkflowServices
from flashcards_generator.interfaces.tui.widgets.shortcut_input import (
    ShortcutInput as Input,
)

DIFFICULTY_OPTIONS: Final = (
    ("Easy - simpler recall", "easy"),
    ("Medium - balanced", "medium"),
    ("Hard - deeper recall", "hard"),
)
QUANTITY_OPTIONS: Final = (
    ("Fewer - shorter deck", "fewer"),
    ("Standard - balanced", "standard"),
    ("More - larger deck", "more"),
)
LANGUAGE_SUGGESTIONS: Final = ("pt_BR", "en", "ja", "zh_Hans")


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
        yield Static(
            "Generation defaults. Shell shortcuts resume outside text fields.",
            classes="workflow-description",
        )
        with Horizontal(classes="action-row"):
            yield Checkbox("Resume", id="settings-resume")
            yield Button(
                "Save settings",
                id="settings-save",
                variant="primary",
            )
        yield Static(
            "Keeps finished chunks after an interrupted large-file run.",
            classes="field-hint",
        )
        yield Label("Input directory", classes="field-label")
        yield Input(
            ".",
            placeholder="Source folder path",
            tooltip="Existing source folder; relative paths use the current directory.",
            id="settings-input-dir",
        )
        yield Static(
            "Source folder; relative paths start from the current directory.",
            classes="field-hint",
        )
        yield Label("Output directory", classes="field-label")
        yield Input(
            "output",
            placeholder="Deck output folder",
            tooltip="Output folder; it is created when generation writes files.",
            id="settings-output-dir",
        )
        yield Static(
            "Deck destination; the folder is created when needed.",
            classes="field-hint",
        )
        yield Label("Generation language", classes="field-label")
        yield Input(
            "pt_BR",
            placeholder="Provider code, e.g. en or pt_BR",
            suggester=SuggestFromList(LANGUAGE_SUGGESTIONS),
            tooltip="Type any NotebookLM-supported language code.",
            id="settings-language",
        )
        yield Static(
            "Provider code; try pt_BR, en, ja, or zh_Hans. Other supported codes work.",
            classes="field-hint",
        )
        yield Label("Difficulty", classes="field-label")
        yield Select[str](
            DIFFICULTY_OPTIONS,
            value="medium",
            allow_blank=False,
            tooltip="Choose the recall depth used to generate cards.",
            id="settings-difficulty",
        )
        yield Label("Quantity", classes="field-label")
        yield Select[str](
            QUANTITY_OPTIONS,
            value="standard",
            allow_blank=False,
            tooltip="Choose the approximate generated deck size.",
            id="settings-quantity",
        )
        yield Label("Instructions", classes="field-label")
        yield Input(
            "",
            placeholder="Optional focus or style guidance",
            tooltip="Optional generation guidance; blank uses provider defaults.",
            id="settings-instructions",
        )
        yield Static(
            "Optional focus or style guidance; blank uses provider defaults.",
            classes="field-hint",
        )
        yield Label("Include pattern", classes="field-label")
        yield Input(
            "",
            placeholder="Optional glob, e.g. chapter*.pdf",
            tooltip="Optional filename glob applied before the exclude pattern.",
            id="settings-include",
        )
        yield Static(
            "Optional filename glob, e.g. chapter*.pdf; blank includes all.",
            classes="field-hint",
        )
        yield Label("Exclude pattern", classes="field-label")
        yield Input(
            "",
            placeholder="Optional glob, e.g. *_old.pdf",
            tooltip="Optional filename glob removed after the include pattern.",
            id="settings-exclude",
        )
        yield Static(
            "Optional filename glob, e.g. *_old.pdf; applied after include.",
            classes="field-hint",
        )
        yield Label("Timeout seconds", classes="field-label")
        yield Input(
            "900",
            placeholder="Positive whole seconds",
            type="integer",
            tooltip="Maximum generation wait in positive whole seconds.",
            id="settings-timeout",
        )
        yield Static(
            "Positive whole seconds (> 0); 900 is 15 minutes.",
            classes="field-hint",
        )
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
            "#settings-instructions": self._settings.instructions,
            "#settings-include": self._settings.include_pattern or "",
            "#settings-exclude": self._settings.exclude_pattern or "",
            "#settings-timeout": self._settings.timeout,
        }
        for selector, value in values.items():
            self.query_one(selector, Input).value = str(value)
        self.query_one(
            "#settings-difficulty", Select
        ).value = self._settings.difficulty
        self.query_one(
            "#settings-quantity", Select
        ).value = self._settings.quantity
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
            difficulty=str(
                self.query_one("#settings-difficulty", Select).value
            ),
            quantity=str(self.query_one("#settings-quantity", Select).value),
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
