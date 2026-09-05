"""Visible validation feedback for the generation form."""

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class GenerationValidationScreen(ModalScreen[None]):
    """Keep invalid generation feedback visible until acknowledged."""

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        Binding("escape", "close_validation", "Close", priority=True),
        Binding("q", "quit_app", "Quit", priority=True),
    ]
    DEFAULT_CSS = """
    GenerationValidationScreen {
        align: center middle;
        background: $background 80%;
    }

    #generation-validation-dialog {
        width: 46;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $panel;
    }

    #generation-validation-title {
        color: $error;
        text-style: bold;
    }

    #generation-validation-message {
        height: auto;
        margin: 1 0;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="generation-validation-dialog"):
            yield Label(
                "Invalid generation options",
                id="generation-validation-title",
            )
            yield Static(self._message, id="generation-validation-message")
            yield Button(
                "Return to form",
                id="dismiss-generation-validation",
                variant="primary",
            )

    def on_mount(self) -> None:
        self.query_one("#dismiss-generation-validation", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dismiss-generation-validation":
            self.dismiss(None)

    def action_close_validation(self) -> None:
        """Return to the generation form."""
        self.dismiss(None)

    def action_quit_app(self) -> None:
        """Preserve the application's global quit behavior."""
        self.app.exit()
