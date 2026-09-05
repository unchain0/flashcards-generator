"""Failing-first acceptance tests for the primary Textual shell."""

from __future__ import annotations

from pathlib import Path

import pytest

from flashcards_generator.interfaces.tui.app import FlashcardsApp
from flashcards_generator.interfaces.tui.widgets import ShortcutInput


@pytest.mark.anyio
async def test_primary_shell_mounts_real_workflow_surfaces() -> None:
    """Given the app mounts, each workflow exposes a real surface."""
    app = FlashcardsApp()

    async with app.run_test(size=(120, 40)):
        assert all(
            app.query_one(selector)
            for selector in (
                "#generate-input-dir",
                "#progress-panel",
                "#results-cards",
                "#merge-folder",
                "#notebooklm-auth-status",
                "#settings-language",
            )
        )


@pytest.mark.anyio
async def test_primary_help_mode_mounts_textual_help_surface() -> None:
    """Given primary --help, Textual mounts its own help modal."""
    app = FlashcardsApp(show_help=True)

    async with app.run_test(size=(120, 40)):
        assert app.screen.query_one("#help-title")


@pytest.mark.anyio
@pytest.mark.parametrize("size", [(120, 40), (52, 24)])
async def test_printable_shortcuts_route_to_every_editable_field(
    size: tuple[int, int],
) -> None:
    """Given a focused field, printable shortcuts stay in its editor."""
    app = FlashcardsApp()
    fields_by_tab = {
        "generate": (
            "#generate-input-dir",
            "#generate-output-dir",
            "#generate-difficulty",
            "#generate-quantity",
            "#generate-language",
            "#generate-instructions",
            "#generate-include",
            "#generate-exclude",
            "#generate-files",
            "#generate-timeout",
        ),
        "merge": ("#merge-folder", "#merge-output"),
        "notebooklm": ("#notebooklm-language",),
        "settings": (
            "#settings-input-dir",
            "#settings-output-dir",
            "#settings-language",
            "#settings-instructions",
            "#settings-include",
            "#settings-exclude",
            "#settings-timeout",
        ),
    }

    async with app.run_test(size=size) as pilot:
        tabs = app.query_one("#main-tabs")
        for tab_id, selectors in fields_by_tab.items():
            tabs.active = tab_id
            await pilot.pause()
            for selector in selectors:
                field = app.query_one(selector, ShortcutInput)
                field.value = ""
                field.focus()
                await pilot.press("q", "g", "r", "m", "n", "s", "?")
                expected = "" if selector == "#settings-timeout" else "qgrmns?"
                assert field.value == expected
                assert tabs.active == tab_id
                assert not app.screen.query("#help-title")


@pytest.mark.anyio
async def test_escape_closes_help_modal() -> None:
    """Given help is open, Escape closes the modal."""
    app = FlashcardsApp(show_help=True)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("escape")
        assert not app.screen.query("#help-title")


@pytest.mark.anyio
async def test_primary_shell_shortcuts_work_outside_inputs() -> None:
    """Given no editor is focused, shortcuts navigate and open help."""
    app = FlashcardsApp()

    async with app.run_test(size=(120, 40)) as pilot:
        app.set_focus(None)
        for key, tab_id in (
            ("g", "generate"),
            ("r", "results"),
            ("m", "merge"),
            ("n", "notebooklm"),
            ("s", "settings"),
        ):
            await pilot.press(key)
            assert app.query_one("#main-tabs").active == tab_id
        await pilot.press("?")
        assert app.screen.query_one("#help-title")


@pytest.mark.anyio
async def test_quit_shortcut_works_outside_inputs() -> None:
    """Given no editor is focused, q exits the application."""
    app = FlashcardsApp()

    async with app.run_test(size=(52, 24)) as pilot:
        app.set_focus(None)
        await pilot.press("q")
        assert not app.is_running


@pytest.mark.anyio
async def test_modifier_shortcut_works_inside_input(tmp_path: Path) -> None:
    """Given an editor is focused, Ctrl+R still refreshes sources."""
    missing_path = tmp_path / "missing"
    app = FlashcardsApp()

    async with app.run_test(size=(120, 40)) as pilot:
        field = app.query_one("#generate-input-dir", ShortcutInput)
        field.value = str(missing_path)
        field.focus()
        await pilot.press("ctrl+r")
        assert (
            "not found"
            in str(app.query_one("#progress-status").render()).lower()
        )
