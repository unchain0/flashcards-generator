"""Pilot coverage for overlapping NotebookLM management actions."""

from threading import Event

import anyio
import pytest

from flashcards_generator.application.dto.workflow import (
    AuthStatus,
    CleanupOutcome,
)
from flashcards_generator.interfaces.tui.app import FlashcardsApp


class OverlapManagementServices:
    """Management fake that records attempts to overlap workers."""

    def __init__(self) -> None:
        self.login_started = Event()
        self.release_login = Event()
        self.login_finished = Event()
        self.refresh_called = Event()

    def auth_status(self) -> AuthStatus:
        self.refresh_called.set()
        return AuthStatus(False, "login required")

    def login(self) -> AuthStatus:
        self.login_started.set()
        self.release_login.wait(5)
        self.login_finished.set()
        return AuthStatus(True, "authenticated")

    def cleanup_all(self, *, confirmed: bool) -> CleanupOutcome:
        return CleanupOutcome(deleted=0, failed=0)

    def set_language(self, language: str) -> bool:
        return True

    def cancel_management(self) -> None:
        self.release_login.set()


@pytest.mark.anyio
async def test_rapid_second_management_action_is_ignored() -> None:
    services = OverlapManagementServices()
    app = FlashcardsApp(services=services)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("n")
        await pilot.click("#notebooklm-login")
        assert await anyio.to_thread.run_sync(services.login_started.wait, 5)

        await pilot.click("#notebooklm-refresh")
        await pilot.pause()

        assert not services.refresh_called.is_set()
        services.release_login.set()
        assert await anyio.to_thread.run_sync(services.login_finished.wait, 5)
