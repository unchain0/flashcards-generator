"""Reusable filesystem picker widgets."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button, DirectoryTree

from flashcards_generator.interfaces.tui.widgets.shortcut_input import (
    ShortcutInput as Input,
)


class DirectoryOnlyTree(DirectoryTree):
    """Directory tree that keeps empty and unusually named folders visible."""

    BINDINGS: ClassVar[
        list[Binding | tuple[str, str] | tuple[str, str, str]]
    ] = [
        *DirectoryTree.BINDINGS,
        Binding("backspace", "select_parent", "Parent"),
        Binding("home", "select_root", "Root"),
    ]

    def filter_paths(self, paths: Iterable[Path]) -> list[Path]:
        """Display directories only without normalizing their names."""
        return [path for path in paths if path.is_dir()]

    def action_select_parent(self) -> None:
        """Select the parent of the directory currently used as the root."""
        current = Path(self.path).expanduser().absolute()
        self._select_directory(current.parent)

    def action_select_root(self) -> None:
        """Select the filesystem root containing the current directory."""
        current = Path(self.path).expanduser().absolute()
        self._select_directory(Path(current.anchor))

    def _select_directory(self, path: Path) -> None:
        if path != self.path:
            self.post_message(self.DirectorySelected(self.root, path))


class SourcePicker(Vertical):
    """Path input paired with an optional directory browser."""

    class Selected(Message):
        """A directory was selected by the user."""

        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(
        self,
        path: Path | str = ".",
        *,
        input_id: str = "source-picker-path",
        id: str | None = None,
        label: str = "Browse directories",
    ) -> None:
        super().__init__(id=id, classes="source-picker")
        self._initial_path = Path(path)
        self._input_id = input_id
        self._label = label

    def compose(self) -> ComposeResult:
        """Render a path field and directory tree."""
        yield Input(
            str(self._initial_path),
            placeholder="Directory path",
            id=self._input_id,
        )
        yield Button(self._label, id="source-picker-browse", variant="default")
        yield DirectoryOnlyTree(
            self._tree_root(self._initial_path),
            id="source-picker-tree",
            classes="is-hidden",
        )

    @property
    def value(self) -> str:
        """Return the path exactly as displayed in the input."""
        return self.query_one(f"#{self._input_id}", Input).value

    def set_path(self, path: Path | str) -> None:
        """Set a path without stripping Unicode, spaces, or empty folders."""
        value = str(path)
        self.query_one(f"#{self._input_id}", Input).value = value
        tree = self.query_one("#source-picker-tree", DirectoryOnlyTree)
        tree.path = self._tree_root(Path(value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Toggle the compact directory browser."""
        if event.button.id == "source-picker-browse":
            self.query_one("#source-picker-tree").toggle_class("is-hidden")

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        """Preserve the selected directory's exact platform representation."""
        self.set_path(event.path)
        self.post_message(self.Selected(event.path))

    @staticmethod
    def _tree_root(path: Path) -> Path:
        if path.exists() and path.is_dir():
            return path
        parent = path.parent
        return parent if parent.exists() else Path.cwd()
