"""Tests for CLI cleanup command."""

from unittest.mock import MagicMock, patch

from flashcards_generator.interfaces.cli import CLI


class TestCLICleanup:
    """Test the cleanup subcommand."""

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.NotebookLMAdapter")
    def test_cleanup_with_days(self, mock_adapter_class, mock_check_auth, tmp_path):
        """Test cleanup command with --days option."""
        mock_check_auth.return_value = True

        mock_adapter = MagicMock()
        mock_adapter.delete_all_notebooks.return_value = (5, 0)
        mock_adapter_class.return_value = mock_adapter

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup", "--days", "7"]):
            result = cli.run()

        assert result == 0
        mock_adapter.delete_all_notebooks.assert_called_once_with(days=7)

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.NotebookLMAdapter")
    def test_cleanup_with_all(self, mock_adapter_class, mock_check_auth, tmp_path):
        """Test cleanup command with --all option."""
        mock_check_auth.return_value = True

        mock_adapter = MagicMock()
        mock_adapter.delete_all_notebooks.return_value = (10, 0)
        mock_adapter_class.return_value = mock_adapter

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup", "--all"]):
            result = cli.run()

        assert result == 0
        mock_adapter.delete_all_notebooks.assert_called_once_with()

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.NotebookLMAdapter")
    def test_cleanup_no_options_shows_error(
        self, mock_adapter_class, mock_check_auth, tmp_path
    ):
        """Test cleanup command without --days or --all shows error."""
        mock_check_auth.return_value = True

        mock_adapter = MagicMock()
        mock_adapter_class.return_value = mock_adapter

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup"]):
            result = cli.run()

        assert result == 1
        mock_adapter.delete_all_notebooks.assert_not_called()

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.NotebookLMAdapter")
    def test_cleanup_partial_failure(
        self, mock_adapter_class, mock_check_auth, tmp_path
    ):
        """Test cleanup when some notebooks fail to delete."""
        mock_check_auth.return_value = True

        mock_adapter = MagicMock()
        mock_adapter.delete_all_notebooks.return_value = (3, 2)
        mock_adapter_class.return_value = mock_adapter

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup", "--all"]):
            result = cli.run()

        # Returns 0 when some succeeded
        assert result == 0

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.NotebookLMAdapter")
    def test_cleanup_complete_failure(
        self, mock_adapter_class, mock_check_auth, tmp_path
    ):
        """Test cleanup when all notebooks fail to delete."""
        mock_check_auth.return_value = True

        mock_adapter = MagicMock()
        mock_adapter.delete_all_notebooks.return_value = (0, 5)
        mock_adapter_class.return_value = mock_adapter

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup", "--all"]):
            result = cli.run()

        # Returns 1 when all failed
        assert result == 1

    @patch.object(CLI, "check_auth")
    def test_cleanup_not_authenticated(self, mock_check_auth):
        """Test cleanup when not authenticated."""
        mock_check_auth.return_value = False

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup", "--all"]):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.NotebookLMAdapter")
    def test_cleanup_skip_auth_check(self, mock_adapter_class, mock_check_auth):
        """Test cleanup with --skip-auth-check flag."""
        mock_check_auth.return_value = False  # Would fail without skip

        mock_adapter = MagicMock()
        mock_adapter.delete_all_notebooks.return_value = (5, 0)
        mock_adapter_class.return_value = mock_adapter

        cli = CLI()

        with patch("sys.argv", ["cli", "cleanup", "--all", "--skip-auth-check"]):
            result = cli.run()

        assert result == 0
        mock_check_auth.assert_not_called()


class TestCLINoCommand:
    """Test CLI behavior when no command is specified."""

    def test_no_command_shows_help(self):
        """Test that running without command shows help and returns error."""
        cli = CLI()

        with patch("sys.argv", ["cli"]):
            result = cli.run()

        assert result == 1

    @patch.object(CLI, "check_auth")
    @patch("flashcards_generator.interfaces.cli.GenerateFlashcardsUseCase")
    def test_default_to_generate_with_args(
        self, mock_use_case_class, mock_check_auth, tmp_path
    ):
        """Test that when command is 'generate' it works normally."""
        mock_check_auth.return_value = True

        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = []
        mock_use_case_class.return_value = mock_use_case

        input_dir = tmp_path / "input"
        input_dir.mkdir()

        cli = CLI()

        with patch(
            "sys.argv",
            ["cli", "generate", "--input-dir", str(input_dir), "--skip-auth-check"],
        ):
            result = cli.run()

        assert result == 0
