import subprocess
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.interfaces.cli import CLI, main


def _assert_custom_request_paths(request, input_dir, output_dir) -> None:
    """Assert path and generation settings from custom CLI arguments."""
    assert request.input_dir == input_dir
    assert request.output_dir == output_dir
    assert request.difficulty == "hard"
    assert request.quantity == "more"


def _assert_custom_request_filters(request) -> None:
    """Assert filtering and instruction settings from custom CLI arguments."""
    assert request.instructions == "Foque em conceitos avançados"
    assert request.timeout == 1800
    assert request.include_pattern == "capitulo*.pdf"
    assert request.exclude_pattern == "*_old.pdf"
    assert request.explicit_files == ["chapter1.pdf", "chapter2.pdf"]


class TestCLI:
    def test_create_parser(self):
        cli = CLI()
        parser = cli._create_parser()

        assert parser is not None

    def test_parser_rejects_removed_no_resume_flag(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        cli = CLI()
        with pytest.raises(SystemExit):
            cli.parser.parse_args([
                "generate",
                "--input-dir",
                str(input_dir),
                "--no-resume",
            ])

    def test_create_request_keeps_resume_enabled_by_default(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        cli = CLI()
        args = cli.parser.parse_args([
            "generate",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ])

        request = cli._create_request(args)

        assert request.resume is True

    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    @patch.object(CLI, "_create_adapter")
    def test_create_use_case_always_wires_chunk_state_repository(
        self, mock_create_adapter, mock_use_case_class, tmp_path
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        cli = CLI()
        args = cli.parser.parse_args([
            "generate",
            "--input-dir",
            str(input_dir),
        ])
        mock_generator = MagicMock()
        mock_create_adapter.return_value = mock_generator

        cli._create_use_case(args)

        mock_use_case_class.assert_called_once()
        assert (
            mock_use_case_class.call_args.kwargs["generator"] is mock_generator
        )
        assert (
            mock_use_case_class.call_args.kwargs["chunk_state_repository"]
            is not None
        )

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_check_auth_success(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Storage exists ✓", stderr=""
        )

        cli = CLI()
        result = cli.check_auth()

        assert result is True

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_check_auth_failure(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error"
        )

        cli = CLI()
        result = cli.check_auth()

        assert result is False

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_check_auth_exception(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="test", timeout=10
        )

        cli = CLI()
        result = cli.check_auth()

        assert result is False

    @patch.object(CLI, "_set_language")
    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_success(
        self, mock_use_case_class, mock_check_auth, mock_set_language, tmp_path
    ):
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_deck = MagicMock()
        mock_deck.name = "Historia"
        mock_deck.total_cards = 5
        mock_deck.flashcards = [1, 2, 3, 4, 5]
        mock_use_case.execute.return_value = [mock_deck]
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        cli = CLI()

        with patch(
            "sys.argv",
            [
                "cli",
                "generate",
                "--input-dir",
                str(input_dir),
                "--skip-auth-check",
            ],
        ):
            result = cli.run()

        assert result == 0
        mock_use_case_class.assert_called_once()

    @patch.object(CLI, "check_auth")
    def test_run_input_dir_not_exists(self, mock_check_auth, tmp_path):
        mock_check_auth.return_value = True

        cli = CLI()

        with patch(
            "sys.argv",
            ["cli", "generate", "--input-dir", str(tmp_path / "nonexistent")],
        ):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "check_auth")
    def test_run_not_authenticated(self, mock_check_auth, tmp_path):
        mock_check_auth.return_value = False

        input_dir = tmp_path / "input"
        input_dir.mkdir()

        cli = CLI()

        with patch(
            "sys.argv", ["cli", "generate", "--input-dir", str(input_dir)]
        ):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "_set_language")
    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_authenticated_success(
        self,
        mock_use_case_class,
        mock_check_auth,
        mock_set_language,
        tmp_path,
    ):
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        cli = CLI()

        with patch(
            "sys.argv", ["cli", "generate", "--input-dir", str(input_dir)]
        ):
            result = cli.run()

        assert result == 0
        mock_check_auth.assert_called_once()

    @patch.object(CLI, "_set_language")
    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_generate_returns_failure_for_processing_errors(
        self,
        mock_use_case_class,
        mock_check_auth,
        mock_set_language,
        tmp_path,
    ):
        """The CLI must expose failed document processing via its exit code."""
        mock_check_auth.return_value = True
        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case.last_run_had_errors = True
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        cli = CLI()

        with patch(
            "sys.argv", ["cli", "generate", "--input-dir", str(input_dir)]
        ):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "_set_language")
    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_with_custom_options(
        self,
        mock_use_case_class,
        mock_check_auth,
        mock_set_language,
        tmp_path,
    ):
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        output_dir = tmp_path / "output"

        cli = CLI()

        with patch(
            "sys.argv",
            [
                "cli",
                "generate",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--difficulty",
                "hard",
                "--quantity",
                "more",
                "--instructions",
                "Foque em conceitos avançados",
                "--language",
                "en_US",
                "--timeout",
                "1800",
                "--include",
                "capitulo*.pdf",
                "--exclude",
                "*_old.pdf",
                "--files",
                "chapter1.pdf, chapter2.pdf",
                "--no-wait",
                "--skip-auth-check",
            ],
        ):
            result = cli.run()

        assert result == 0
        mock_use_case_class.assert_called_once()
        request = mock_use_case.execute.call_args.args[0]
        _assert_custom_request_paths(request, input_dir, output_dir)
        _assert_custom_request_filters(request)
        assert request.wait_for_completion is False
        mock_set_language.assert_called_once_with("en_US")

    @patch.object(CLI, "_set_language")
    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_empty_decks(
        self, mock_use_case_class, mock_check_auth, mock_set_language, tmp_path
    ):
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        cli = CLI()

        with patch(
            "sys.argv",
            [
                "cli",
                "generate",
                "--input-dir",
                str(input_dir),
                "--skip-auth-check",
            ],
        ):
            result = cli.run()

        assert result == 0

    @patch.object(CLI, "_set_language")
    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_deck_without_flashcards(
        self, mock_use_case_class, mock_check_auth, mock_set_language, tmp_path
    ):
        mock_check_auth.return_value = True

        mock_deck = MagicMock()
        mock_deck.name = "Tema1"
        mock_deck.total_cards = 0
        mock_deck.flashcards = []

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = [mock_deck]
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        cli = CLI()

        with patch(
            "sys.argv",
            [
                "cli",
                "generate",
                "--input-dir",
                str(input_dir),
                "--skip-auth-check",
            ],
        ):
            result = cli.run()

        assert result == 0


class TestMain:
    @patch("flashcards_generator.interfaces.cli.CLI")
    def test_main(self, mock_cli_class):
        mock_cli = MagicMock()
        mock_cli.run.return_value = 0
        mock_cli_class.return_value = mock_cli

        with (
            patch("sys.argv", ["main"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        mock_cli.run.assert_called_once()

    @patch("flashcards_generator.interfaces.cli.CLI")
    def test_main_error(self, mock_cli_class):
        mock_cli = MagicMock()
        mock_cli.run.return_value = 1
        mock_cli_class.return_value = mock_cli

        with (
            patch("sys.argv", ["main"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_set_language_timeout(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 10)

        cli = CLI()
        # Should not raise exception
        cli._set_language("pt")

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_set_language_file_not_found(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        mock_run.side_effect = FileNotFoundError()

        cli = CLI()
        # Should not raise exception
        cli._set_language("pt")

    @pytest.mark.parametrize(
        "selector",
        [
            ["--all", "--days", "7"],
            ["--days", "0"],
            ["--days", "-1"],
        ],
    )
    def test_cleanup_selectors_are_mutually_exclusive_and_positive(
        self, selector
    ):
        with pytest.raises(SystemExit) as exc_info:
            CLI().parser.parse_args(["cleanup", *selector])

        assert exc_info.value.code == 2

    def test_validate_input_rejects_regular_file(self, tmp_path):
        input_file = tmp_path / "input.pdf"
        input_file.touch()

        assert CLI()._validate_input(input_file) is False

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_check_auth_uses_successful_exit_status(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Authenticated", stderr=""
        )

        assert CLI().check_auth() is True

    @patch("flashcards_generator.interfaces.cli.logger")
    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_check_auth_reports_os_error_context(
        self, mock_run, mock_find, mock_logger
    ):
        mock_find.return_value = "notebooklm"
        mock_run.side_effect = PermissionError("denied")

        assert CLI().check_auth() is False
        mock_logger.error.assert_called_once_with(
            "Não foi possível verificar autenticação: denied"
        )

    @patch("flashcards_generator.interfaces.cli.logger")
    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_set_language_reports_nonzero_exit(
        self, mock_run, mock_find, mock_logger
    ):
        mock_find.return_value = "notebooklm"
        mock_run.return_value = MagicMock(
            returncode=2, stdout="", stderr="unsupported language"
        )

        CLI()._set_language("invalid")

        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_called_once_with(
            "Não foi possível configurar o idioma: unsupported language"
        )

    def test_generate_parses_anki_connect_options(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        args = CLI().parser.parse_args([
            "generate",
            "--input-dir",
            str(input_dir),
            "--anki-deck",
            "Estácio::Disciplina::Unidade 1",
            "--anki-connect-url",
            "http://127.0.0.1:8765",
        ])

        assert args.anki_deck == "Estácio::Disciplina::Unidade 1"
        assert args.anki_connect_url == "http://127.0.0.1:8765"
