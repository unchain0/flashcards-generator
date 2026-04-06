"""Tests for CLI merge command."""

import csv
from unittest.mock import patch

from flashcards_generator.domain.exceptions import CSVMergeError
from flashcards_generator.interfaces.cli import CLI


class TestCLIMerge:
    """Test the merge subcommand."""

    @patch("flashcards_generator.interfaces.cli.CsvMerger.merge")
    def test_merge_command_success(self, mock_merge, tmp_path):
        """Test merge command with valid CSV files."""
        mock_merge.return_value = 5

        csv_file = tmp_path / "test.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Q1", "A1"])

        cli = CLI()
        with patch("sys.argv", ["cli", "merge", "--folder", str(tmp_path)]):
            result = cli.run()

        assert result == 0

    @patch("flashcards_generator.interfaces.cli.CsvMerger.merge")
    def test_merge_command_folder_not_exists(self, mock_merge, tmp_path):
        """Test merge command returns 1 when folder doesn't exist."""
        mock_merge.side_effect = CSVMergeError(
            tmp_path / "nonexistent", "Folder does not exist"
        )

        cli = CLI()
        with patch(
            "sys.argv", ["cli", "merge", "--folder", str(tmp_path / "nonexistent")]
        ):
            result = cli.run()

        assert result == 1

    @patch("flashcards_generator.interfaces.cli.CsvMerger.merge")
    def test_merge_command_with_deduplication(self, mock_merge, tmp_path):
        """Test merge command with -d flag for deduplication."""
        mock_merge.return_value = 3

        csv_file = tmp_path / "test.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Q1", "A1"])

        cli = CLI()
        with patch("sys.argv", ["cli", "merge", "--folder", str(tmp_path), "-d"]):
            result = cli.run()

        assert result == 0

    @patch("flashcards_generator.interfaces.cli.CsvMerger.merge")
    def test_merge_command_custom_output(self, mock_merge, tmp_path):
        """Test merge command with -o flag for custom output filename."""
        mock_merge.return_value = 5

        csv_file = tmp_path / "test.csv"
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Q1", "A1"])

        cli = CLI()
        with patch(
            "sys.argv",
            ["cli", "merge", "--folder", str(tmp_path), "-o", "custom.csv"],
        ):
            result = cli.run()

        assert result == 0

    @patch("flashcards_generator.interfaces.cli.CsvMerger.merge")
    def test_merge_command_no_csv_files(self, mock_merge, tmp_path):
        """Test merge command returns 1 when no CSV files found."""
        mock_merge.side_effect = CSVMergeError(tmp_path, "No CSV files found in folder")

        cli = CLI()
        with patch("sys.argv", ["cli", "merge", "--folder", str(tmp_path)]):
            result = cli.run()

        assert result == 1

    @patch("flashcards_generator.interfaces.cli.CsvMerger.merge")
    @patch("flashcards_generator.interfaces.cli.logger")
    def test_merge_command_logs_success(self, mock_logger, mock_merge, tmp_path):
        """Test merge command logs success message."""
        mock_merge.return_value = 10

        test_folder = tmp_path / "test_merge"
        test_folder.mkdir()

        cli = CLI()
        with patch("sys.argv", ["cli", "merge", "--folder", str(test_folder)]):
            cli.run()

        mock_logger.info.assert_called()
