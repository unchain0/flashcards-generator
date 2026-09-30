from pathlib import Path
from unittest.mock import patch

import pytest

from flashcards_generator.domain_models.exceptions import CSVMergeError
from flashcards_generator.integrations.csv_merger import CsvMerger
from flashcards_generator.services.dto.merge_request import MergeCsvRequest


def test_atomic_replace_failure_preserves_output_and_removes_temporary(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.csv").write_text(
        '"New front","New back"\n', encoding="utf-8"
    )
    output = tmp_path / "merged_flashcards.csv"
    output.write_text("previous result\n", encoding="utf-8")

    with (
        patch.object(Path, "replace", side_effect=OSError("replace failed")),
        pytest.raises(CSVMergeError, match="replace failed"),
    ):
        CsvMerger.merge(MergeCsvRequest(folder_path=tmp_path))

    assert output.read_text(encoding="utf-8") == "previous result\n"
    assert list(tmp_path.glob(".merged_flashcards.csv.*.tmp")) == []


def test_temporary_creation_failure_preserves_existing_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text('"New front","New back"\n', encoding="utf-8")
    output = tmp_path / "merged_flashcards.csv"
    output.write_text("previous result\n", encoding="utf-8")
    error = OSError("temporary file creation failed")

    with (
        patch(
            "flashcards_generator.integrations.csv_merger.tempfile.NamedTemporaryFile",
            side_effect=error,
        ),
        pytest.raises(
            CSVMergeError, match="temporary file creation failed"
        ) as failure,
    ):
        CsvMerger.merge(MergeCsvRequest(folder_path=tmp_path))

    assert failure.value.__cause__ is error
    assert output.read_text(encoding="utf-8") == "previous result\n"
    assert source.read_text(encoding="utf-8") == '"New front","New back"\n'
    assert list(tmp_path.glob(".merged_flashcards.csv.*.tmp")) == []
