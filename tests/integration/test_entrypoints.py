from __future__ import annotations

import importlib.metadata
import importlib.util
from unittest.mock import patch


def test_web_and_local_companion_console_scripts_are_installed() -> None:
    scripts = {
        entry_point.name: entry_point.value
        for entry_point in importlib.metadata.entry_points().select(
            group="console_scripts"
        )
        if entry_point.name.startswith("flashcards")
    }

    assert scripts == {
        "flashcards-companion": "flashcards_generator.delivery.companion.main:main",
        "flashcards-web": "flashcards_generator.delivery.web.main:main",
    }


def test_legacy_cli_and_tui_modules_are_not_importable() -> None:
    assert (
        importlib.util.find_spec("flashcards_generator.delivery.cli") is None
    )
    assert (
        importlib.util.find_spec("flashcards_generator.delivery.tui") is None
    )


def test_primary_module_delegates_to_web_server() -> None:
    from flashcards_generator.delivery import main

    assert main.web_main.__module__ == (
        "flashcards_generator.delivery.web.main"
    )


def test_primary_entrypoint_invokes_web_server() -> None:
    from flashcards_generator.delivery import main

    with patch.object(main, "web_main") as web_main:
        main.main()

    web_main.assert_called_once_with()
