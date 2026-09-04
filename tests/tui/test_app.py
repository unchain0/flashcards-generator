"""Failing-first acceptance tests for the primary Textual shell."""

from __future__ import annotations

import pytest

from flashcards_generator.interfaces.tui.app import FlashcardsApp


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
async def test_help_escape_closes_modal_and_input_focus_keeps_shortcuts() -> (
    None
):
    """Given a focused field, global navigation and modal escape still work."""
    app = FlashcardsApp()

    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#merge-folder").focus()
        await pilot.pause()
        await pilot.press("n")
        assert app.query_one("#main-tabs").active == "notebooklm"
        await pilot.press("s")
        assert app.query_one("#main-tabs").active == "settings"
        await pilot.press("?")
        assert app.screen.query_one("#help-title")
        await pilot.press("escape")
        await pilot.pause()
        assert not app.screen.query("#help-title")


@pytest.mark.anyio
async def test_primary_shell_shortcuts_navigate_workflows() -> None:
    """Given the shell is mounted, shortcuts select each workflow."""
    app = FlashcardsApp()

    async with app.run_test(size=(120, 40)) as pilot:
        for key, tab_id in (
            ("g", "generate"),
            ("r", "results"),
            ("m", "merge"),
            ("n", "notebooklm"),
            ("s", "settings"),
        ):
            await pilot.press(key)
            assert app.query_one("#main-tabs").active == tab_id
