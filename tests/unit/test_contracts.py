"""Contract tests for public domain and request models."""

import subprocess
import sys

import pytest
from pydantic import ValidationError

from flashcards_generator.application.dto.merge_request import MergeCsvRequest


def test_generation_result_constructs_from_clean_import():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from flashcards_generator.domain.ports.flashcard_generator "
                "import GenerationResult; "
                "assert GenerationResult(deck={'name': 'audit'}).deck.name == "
                "'audit'"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "filename", ["", "../outside.csv", "/tmp/outside.csv", "nested/out.csv"]
)
def test_merge_output_filename_must_be_relative_basename(tmp_path, filename):
    with pytest.raises(ValidationError):
        MergeCsvRequest(folder_path=tmp_path, output_filename=filename)
