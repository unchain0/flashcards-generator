import csv
from unittest.mock import patch

import pytest

from flashcards_generator.application.csv_merger import CsvMerger
from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.domain.exceptions import CSVMergeError


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

        with open(output, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            content = list(reader)
            assert len(content) == 3
            assert content[0] == ["A Front 1", "A Back 1"]
            assert content[1] == ["B Front 1", "B Back 1"]
            assert content[2] == ["B Front 2", "B Back 2"]

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
        request = MergeCsvRequest(folder_path=tmp_path, output_filename=custom_name)
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
