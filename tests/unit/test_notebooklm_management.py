import subprocess
from unittest.mock import patch

from flashcards_generator.integrations.notebooklm.gateway import (
    NotebookLMAdapter,
)
from flashcards_generator.integrations.notebooklm.management import (
    NotebookLMManagement,
)
from flashcards_generator.services.dto.workflow import AuthStatus


def test_login_reports_cancellation_when_command_start_fails() -> None:
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )

    def cancel_then_fail_to_start(
        _arguments: list[str],
        *,
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str] | None:
        manager.cancel_active()
        return None

    with patch.object(manager, "_run", side_effect=cancel_then_fail_to_start):
        assert manager.login() == AuthStatus(False, "login cancelled")


def test_login_maps_successful_command() -> None:
    manager = NotebookLMManagement(
        "notebooklm",
        lambda timeout: NotebookLMAdapter("notebooklm", timeout=timeout),
    )

    with patch.object(
        manager,
        "_run",
        return_value=subprocess.CompletedProcess(["login"], 0, "", ""),
    ) as run:
        assert manager.login() == AuthStatus(True, "authenticated")

    run.assert_called_once_with(["login"], timeout=None)
