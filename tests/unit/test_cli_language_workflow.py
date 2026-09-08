"""Regression coverage for CLI language forwarding to shared workflows."""

from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.interfaces.cli import CLI


@pytest.mark.parametrize("language", ["pt_BR", "en_US"])
def test_cli_configures_requested_language_once(tmp_path, language):
    use_case = MagicMock()
    use_case.execute.return_value = []
    use_case.last_run_had_errors = False
    with (
        patch.object(CLI, "_set_language") as legacy_language,
        patch(
            "flashcards_generator.interfaces.composition."
            "NotebookLMManagement.set_language",
            return_value=True,
        ) as set_language,
        patch(
            "flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase",
            return_value=use_case,
        ),
        patch(
            "sys.argv",
            [
                "cli",
                "generate",
                "--input-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "output"),
                "--skip-auth-check",
                "--language",
                language,
            ],
        ),
    ):
        assert CLI().run() == 0

    set_language.assert_called_once_with(language)
    legacy_language.assert_not_called()
    assert use_case.execute.call_args.args[0].language == language


def test_cli_stops_generation_when_language_configuration_fails(tmp_path):
    with (
        patch.object(CLI, "_set_language"),
        patch(
            "flashcards_generator.interfaces.composition."
            "NotebookLMManagement.set_language",
            return_value=False,
        ),
        patch(
            "flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase"
        ) as use_case,
        patch(
            "sys.argv",
            [
                "cli",
                "generate",
                "--input-dir",
                str(tmp_path),
                "--skip-auth-check",
            ],
        ),
        pytest.raises(RuntimeError, match="Unable to set NotebookLM"),
    ):
        CLI().run()

    use_case.assert_not_called()
