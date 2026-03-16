import subprocess
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.interfaces.cli import CLI, main


class TestCLI:
    def test_create_parser(self):
        cli = CLI()
        parser = cli._create_parser()

        assert parser is not None

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
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        cli = CLI()
        result = cli.check_auth()

        assert result is False

    @patch("flashcards_generator.interfaces.cli.find_notebooklm")
    @patch("flashcards_generator.interfaces.cli.subprocess.run")
    def test_check_auth_exception(self, mock_run, mock_find):
        mock_find.return_value = "notebooklm"
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=10)

        cli = CLI()
        result = cli.check_auth()

        assert result is False

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_success(self, mock_use_case_class, mock_check_auth, tmp_path):
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
            "sys.argv", ["cli", "--input-dir", str(input_dir), "--skip-auth-check"]
        ):
            result = cli.run()

        assert result == 0
        mock_use_case_class.assert_called_once()

    @patch.object(CLI, "check_auth")
    def test_run_input_dir_not_exists(self, mock_check_auth, tmp_path):
        mock_check_auth.return_value = True

        cli = CLI()

        with patch("sys.argv", ["cli", "--input-dir", str(tmp_path / "nonexistent")]):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "check_auth")
    def test_run_not_authenticated(self, mock_check_auth, tmp_path):
        mock_check_auth.return_value = False

        input_dir = tmp_path / "input"
        input_dir.mkdir()

        cli = CLI()

        with patch("sys.argv", ["cli", "--input-dir", str(input_dir)]):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_authenticated_success(
        self, mock_use_case_class, mock_check_auth, tmp_path
    ):
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        cli = CLI()

        with patch("sys.argv", ["cli", "--input-dir", str(input_dir)]):
            result = cli.run()

        assert result == 0
        mock_check_auth.assert_called_once()

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_with_custom_options(
        self, mock_use_case_class, mock_check_auth, tmp_path
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
                "--timeout",
                "1800",
                "--skip-auth-check",
            ],
        ):
            result = cli.run()

        assert result == 0
        mock_use_case_class.assert_called_once()

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_empty_decks(self, mock_use_case_class, mock_check_auth, tmp_path):
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "tema1").mkdir()

        cli = CLI()

        with patch(
            "sys.argv", ["cli", "--input-dir", str(input_dir), "--skip-auth-check"]
        ):
            result = cli.run()

        assert result == 0

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_run_deck_without_flashcards(
        self, mock_use_case_class, mock_check_auth, tmp_path
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
            "sys.argv", ["cli", "--input-dir", str(input_dir), "--skip-auth-check"]
        ):
            result = cli.run()

        assert result == 0


class TestMain:
    @patch("flashcards_generator.interfaces.cli.CLI")
    def test_main(self, mock_cli_class):
        mock_cli = MagicMock()
        mock_cli.run.return_value = 0
        mock_cli_class.return_value = mock_cli

        with patch("sys.argv", ["main"]), pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        mock_cli.run.assert_called_once()

    @patch("flashcards_generator.interfaces.cli.CLI")
    def test_main_error(self, mock_cli_class):
        mock_cli = MagicMock()
        mock_cli.run.return_value = 1
        mock_cli_class.return_value = mock_cli

        with patch("sys.argv", ["main"]), pytest.raises(SystemExit) as exc_info:
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
