import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from flashcards_generator.application.csv_merger import CsvMerger
from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.domain.exceptions import CSVMergeError


def _assert_merged_csv(output: Path, expected_rows: list[list[str]]) -> None:
    """Assert the merged file contains exactly the expected rows."""
    with open(output, newline="", encoding="utf-8") as f:
        content = list(csv.reader(f))
    assert len(content) == len(expected_rows)
    assert content == expected_rows


class TestCsvMerger:
    def test_merge_single_file(self, tmp_path):
        csv_file = tmp_path / "flashcards.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Front 1", "Back 1"])
            writer.writerow(["Front 2", "Back 2"])

        request = MergeCsvRequest(folder_path=tmp_path)
        rows = CsvMerger.merge(request)

        assert rows == 2
        output = tmp_path / "merged_flashcards.csv"
        assert output.exists()

        with open(output, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            content = list(reader)
            assert len(content) == 2
            assert content[0] == ["Front 1", "Back 1"]
            assert content[1] == ["Front 2", "Back 2"]

    def test_merge_multiple_files(self, tmp_path):
        csv_file1 = tmp_path / "a_flashcards.csv"
        with open(csv_file1, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["A Front 1", "A Back 1"])

        csv_file2 = tmp_path / "b_flashcards.csv"
        with open(csv_file2, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["B Front 1", "B Back 1"])
            writer.writerow(["B Front 2", "B Back 2"])

        request = MergeCsvRequest(folder_path=tmp_path)
        rows = CsvMerger.merge(request)

        assert rows == 3
        output = tmp_path / "merged_flashcards.csv"
        assert output.exists()
        _assert_merged_csv(
            output,
            [
                ["A Front 1", "A Back 1"],
                ["B Front 1", "B Back 1"],
                ["B Front 2", "B Back 2"],
            ],
        )

    def test_detailed_merge_reports_deduplication_counts(self, tmp_path):
        (tmp_path / "one.csv").write_text("Q1,A1\nQ2,A2\n", encoding="utf-8")
        (tmp_path / "two.csv").write_text("Q1,A1\nQ3,A3\n", encoding="utf-8")

        result = CsvMerger.merge_detailed(
            MergeCsvRequest(folder_path=tmp_path, deduplicate=True)
        )

        assert result.rows_before == 4
        assert result.rows_written == 3
        assert result.duplicates_removed == 1

    def test_merge_with_deduplication(self, tmp_path):
        csv_file1 = tmp_path / "flashcards1.csv"
        with open(csv_file1, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Duplicate Front", "Duplicate Back"])
            writer.writerow(["Unique 1", "Unique Back 1"])

        csv_file2 = tmp_path / "flashcards2.csv"
        with open(csv_file2, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Duplicate Front", "Duplicate Back"])
            writer.writerow(["Unique 2", "Unique Back 2"])

        request = MergeCsvRequest(folder_path=tmp_path, deduplicate=True)
        rows = CsvMerger.merge(request)

        assert rows == 3
        output = tmp_path / "merged_flashcards.csv"
        assert output.exists()

        with open(output, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            content = list(reader)
            assert len(content) == 3

    def test_merge_folder_not_exists(self):
        non_existent_path = "/path/that/does/not/exist"
        request = MergeCsvRequest(folder_path=non_existent_path)

        with pytest.raises(CSVMergeError) as exc_info:
            CsvMerger.merge(request)

        assert "does not exist" in str(exc_info.value)

    def test_merge_no_csv_files(self, tmp_path):
        request = MergeCsvRequest(folder_path=tmp_path)

        with pytest.raises(CSVMergeError) as exc_info:
            CsvMerger.merge(request)

        assert "No CSV files found" in str(exc_info.value)

    def test_merge_excludes_output_file(self, tmp_path):
        input_csv = tmp_path / "flashcards.csv"
        with open(input_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Front", "Back"])

        output_csv = tmp_path / "merged_flashcards.csv"
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Old Front", "Old Back"])

        request = MergeCsvRequest(folder_path=tmp_path)
        rows = CsvMerger.merge(request)

        assert rows == 1
        assert output_csv.exists()

        with open(output_csv, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            content = list(reader)
            assert len(content) == 1
            assert content[0] == ["Front", "Back"]

    def test_merge_rejects_rows_with_extra_columns(self, tmp_path):
        csv_file = tmp_path / "malformed.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, quoting=csv.QUOTE_ALL).writerow([
                "Front",
                "Back",
                "Unexpected tag",
            ])

        with pytest.raises(CSVMergeError) as exc_info:
            CsvMerger.merge(MergeCsvRequest(folder_path=tmp_path))

        assert exc_info.value.folder_path == csv_file
        assert "row 1" in exc_info.value.reason.lower()
        assert "3 columns" in exc_info.value.reason

    def test_merge_preserves_quoted_two_column_rows(self, tmp_path):
        csv_file = tmp_path / "quoted.csv"
        expected_row = [
            "Front, with a tab\tand newline\ncontinued",
            'Back with "quotes"',
        ]
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, quoting=csv.QUOTE_ALL).writerow(expected_row)

        rows = CsvMerger.merge(MergeCsvRequest(folder_path=tmp_path))

        with open(
            tmp_path / "merged_flashcards.csv",
            newline="",
            encoding="utf-8",
        ) as f:
            merged_rows = list(csv.reader(f))

        assert rows == 1
        assert merged_rows == [expected_row]

    def test_merge_handles_short_rows(self, tmp_path):
        csv_file = tmp_path / "flashcards.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Valid Front", "Valid Back"])
            writer.writerow(["Short"])
            writer.writerow(["Valid Front 2", "Valid Back 2"])
            writer.writerow([])

        request = MergeCsvRequest(folder_path=tmp_path)
        rows = CsvMerger.merge(request)

        assert rows == 2
        output = tmp_path / "merged_flashcards.csv"
        assert output.exists()

        with open(output, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            content = list(reader)
            assert len(content) == 2
            assert content[0] == ["Valid Front", "Valid Back"]
            assert content[1] == ["Valid Front 2", "Valid Back 2"]

    def test_merge_custom_output_name(self, tmp_path):
        csv_file = tmp_path / "flashcards.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Front 1", "Back 1"])
            writer.writerow(["Front 2", "Back 2"])

        custom_name = "my_custom_merge.csv"
        request = MergeCsvRequest(
            folder_path=tmp_path, output_filename=custom_name
        )
        rows = CsvMerger.merge(request)

        assert rows == 2
        output = tmp_path / custom_name
        assert output.exists()

        default_output = tmp_path / "merged_flashcards.csv"
        assert not default_output.exists()

        with open(output, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            content = list(reader)
            assert len(content) == 2

    def test_merge_handles_exception(self, tmp_path):
        """Test that CSVMergeError is raised on unexpected exception."""
        csv_file = tmp_path / "flashcards.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Front", "Back"])

        request = MergeCsvRequest(folder_path=tmp_path)

        with patch(
            "flashcards_generator.application.csv_merger.csv.reader"
        ) as mock_reader:
            mock_reader.side_effect = Exception("Unexpected CSV error")

            with pytest.raises(CSVMergeError) as exc_info:
                CsvMerger.merge(request)

            assert "Unexpected CSV error" in str(exc_info.value)

    def test_merge_does_not_publish_partial_output_on_invalid_row(
        self, tmp_path
    ):
        """Malformed input must not replace the previous merged CSV."""
        valid_file = tmp_path / "valid.csv"
        valid_file.write_text(
            '"O {{c1::valid}} front","valid back"\n',
            encoding="utf-8",
        )
        malformed_file = tmp_path / "malformed.csv"
        malformed_file.write_text(
            '"O {{c1::invalid}} front","invalid back","extra"\n',
            encoding="utf-8",
        )
        output = tmp_path / "merged_flashcards.csv"
        output.write_text("previous result\n", encoding="utf-8")

        with pytest.raises(CSVMergeError, match="exactly 2 columns"):
            CsvMerger.merge(MergeCsvRequest(folder_path=tmp_path))

        assert output.read_text(encoding="utf-8") == "previous result\n"
