"""Failing-first Pilot coverage for management, settings, and picker."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import anyio
import pytest
from textual.app import App, ComposeResult

from flashcards_generator.application.dto.workflow import (
    AuthStatus,
    CleanupOutcome,
)
from flashcards_generator.infrastructure.settings import (
    Settings,
    SettingsRepository,
)
from flashcards_generator.interfaces.tui.app import FlashcardsApp
from flashcards_generator.interfaces.tui.widgets.source_picker import (
    DirectoryOnlyTree,
    SourcePicker,
)


class PickerApp(App[None]):
    """Minimal app boundary for SourcePicker keyboard interaction."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        yield SourcePicker(path=self.path, id="source-picker")


class ManagementServices:
    """Fake NotebookLM management and settings boundary."""

    def __init__(self) -> None:
        self.logged_in = False
        self.cleanup_confirmed = False
        self.language: str | None = None
        self.login_called = Event()
        self.cleanup_called = Event()
        self.language_called = Event()

    def auth_status(self) -> AuthStatus:
        return AuthStatus(
            authenticated=self.logged_in,
            message="authenticated" if self.logged_in else "login required",
        )

    def login(self) -> AuthStatus:
        self.logged_in = True
        self.login_called.set()
        return self.auth_status()

    def cleanup_all(self, *, confirmed: bool) -> CleanupOutcome:
        if not confirmed:
            raise AssertionError("cleanup-all requires explicit confirmation")
        self.cleanup_confirmed = True
        self.cleanup_called.set()
        return CleanupOutcome(deleted=2, failed=0)

    def set_language(self, language: str) -> bool:
        self.language = language
        self.language_called.set()
        return True

    def cancel_management(self) -> None:
        return


class BlockingManagementServices(ManagementServices):
    """Management fake that exposes the need for unmount cancellation."""

    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.cancel_requested = Event()

    def login(self) -> AuthStatus:
        self.started.set()
        self.cancel_requested.wait(5)
        return super().login()

    def cancel_management(self) -> None:
        self.cancel_requested.set()


class SettingsServices:
    """Small settings-only service boundary for the Pilot."""

    def __init__(self, repository: SettingsRepository) -> None:
        self.repository = repository

    def load(self) -> Settings:
        return self.repository.load()

    def save(self, settings: Settings) -> None:
        self.repository.save(settings)

    def cancel_management(self) -> None:
        return


@pytest.mark.anyio
async def test_notebooklm_requires_login_and_explicit_cleanup_confirmation(
    tmp_path: Path,
) -> None:
    """Given an unauthenticated provider, login and cleanup are delegated."""
    services = ManagementServices()
    app = FlashcardsApp(services=services)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await pilot.click("#notebooklm-refresh")
        login_required = (
            "login"
            in str(app.query_one("#notebooklm-auth-status").render()).lower()
        )
        await pilot.click("#notebooklm-login")
        login_called = await anyio.to_thread.run_sync(
            services.login_called.wait, 5
        )
        app.query_one("#notebooklm-language").value = "en"
        await pilot.click("#notebooklm-set-language")
        language_called = await anyio.to_thread.run_sync(
            services.language_called.wait, 5
        )
        await pilot.click("#notebooklm-cleanup-all")
        confirmation_open = bool(app.query_one("#cleanup-confirm"))
        not_confirmed = services.cleanup_confirmed is False
        await pilot.click("#confirm-cleanup-all")
        cleanup_called = await anyio.to_thread.run_sync(
            services.cleanup_called.wait, 5
        )
        assert (
            login_required,
            login_called,
            language_called,
            services.language,
            confirmation_open,
            not_confirmed,
            cleanup_called,
            services.cleanup_confirmed,
        ) == (True, True, True, "en", True, True, True, True)


@pytest.mark.anyio
async def test_unmount_cancels_running_notebooklm_management_worker() -> None:
    """Unmounting the TUI requests cancellation of management work."""
    services = BlockingManagementServices()
    app = FlashcardsApp(services=services)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await pilot.click("#notebooklm-login")
        assert await anyio.to_thread.run_sync(services.started.wait, 5)
        app.on_unmount()

    assert services.cancel_requested.is_set()


@pytest.mark.anyio
async def test_picker_keyboard_navigates_from_empty_unusual_directory(
    tmp_path: Path,
) -> None:
    """Parent and Home navigation update the picker from an empty folder."""
    selected = tmp_path / "Álbum com espaço" / "vazio"
    selected.mkdir(parents=True)
    app = PickerApp(selected)

    async with app.run_test(size=(120, 40)) as pilot:
        picker = app.query_one("#source-picker", SourcePicker)
        tree = app.query_one("#source-picker-tree", DirectoryOnlyTree)
        tree.focus()

        await pilot.press("backspace")
        assert picker.value == str(selected.parent)

        await pilot.press("home")
        assert picker.value == selected.anchor


@pytest.mark.anyio
async def test_picker_navigates_and_filters_pdf_pptx_sources(
    tmp_path: Path,
) -> None:
    """Given an unusual directory, picker and source filter preserve files."""
    root = tmp_path / "Álbum com espaço"
    root.mkdir()
    (root / "aula.pdf").touch()
    (root / "slides.pptx").touch()
    (root / "ignore.txt").touch()
    services = object()
    app = FlashcardsApp(services=services)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#source-picker-browse")
        assert app.query_one("#source-picker-tree").display is True
        app.query_one("#generate-input-dir").value = str(root)
        await pilot.click("#generate-refresh")
        source_list = app.query_one("#generate-sources")
        assert source_list.option_count == 2


@pytest.mark.anyio
async def test_settings_panel_persists_edited_preferences(
    tmp_path: Path,
) -> None:
    """Given edited defaults, Save persists values through XDG storage."""
    repository = SettingsRepository(tmp_path / "settings.json")
    app = FlashcardsApp(services=SettingsServices(repository))

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        app.query_one("#settings-language").value = "en"
        app.query_one("#settings-input-dir").value = "docs"
        app.query_one("#settings-output-dir").value = "decks"
        app.query_one("#settings-timeout").value = "120"
        app.query_one("#settings-include").value = "*.pdf"
        app.query_one("#settings-exclude").value = "draft"
        app.query_one("#settings-resume").value = False
        await pilot.click("#settings-save")

    saved = repository.load()
    assert saved == Settings(
        language="en",
        input_dir=Path("docs"),
        output_dir=Path("decks"),
        timeout=120,
        include_pattern="*.pdf",
        exclude_pattern="draft",
        resume=False,
    )


@pytest.mark.anyio
async def test_generate_panel_loads_persisted_preferences(
    tmp_path: Path,
) -> None:
    """Given saved defaults, Generate loads its filters and resume setting."""
    repository = SettingsRepository(tmp_path / "settings.json")
    repository.save(
        Settings(
            language="en",
            input_dir=Path("docs"),
            output_dir=Path("decks"),
            timeout=120,
            include_pattern="*.pdf",
            exclude_pattern="draft",
            resume=False,
        )
    )

    app = FlashcardsApp(services=SettingsServices(repository))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("g")
        assert app.query_one("#generate-include").value == "*.pdf"
        assert app.query_one("#generate-exclude").value == "draft"
        assert app.query_one("#generate-resume").value is False
