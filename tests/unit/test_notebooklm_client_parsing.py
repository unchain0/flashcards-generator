import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.domain_models.entities import Flashcard
from flashcards_generator.domain_models.exceptions import (
    NotebookLMResponseError,
)
from flashcards_generator.integrations.notebooklm.client import (
    NotebookLMClient,
)


class TestNotebookLMClientParsing:
    def test_parse_flashcards(self, tmp_path):
        json_data = [
            {"front": "Question 1?", "back": "Answer 1"},
            {"question": "Question 2?", "answer": "Answer 2"},
            {"q": "Question 3?", "a": "Answer 3"},
        ]
        json_file = tmp_path / "flashcards.json"
        json_file.write_text(json.dumps(json_data))

        result = NotebookLMClient("notebooklm").parse_flashcards(json_file)

        assert result == [
            Flashcard(front="Question 1?", back="Answer 1"),
            Flashcard(front="Question 2?", back="Answer 2"),
            Flashcard(front="Question 3?", back="Answer 3"),
        ]

    def test_parse_flashcards_with_flashcards_key(self, tmp_path):
        json_path = tmp_path / "flashcards.json"
        json_path.write_text(
            json.dumps({"flashcards": [{"front": "Q1?", "back": "A1"}]})
        )

        assert (
            len(NotebookLMClient("notebooklm").parse_flashcards(json_path))
            == 1
        )

    def test_parse_flashcards_empty(self, tmp_path):
        json_path = tmp_path / "flashcards.json"
        json_path.write_text("[]")

        assert NotebookLMClient("notebooklm").parse_flashcards(json_path) == []

    def test_parse_flashcards_invalid_json(self, tmp_path):
        json_path = tmp_path / "flashcards.json"
        json_path.write_text("invalid json")

        with pytest.raises(NotebookLMResponseError, match="invalid JSON"):
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

    def test_parse_flashcards_file_not_found(self, tmp_path):
        json_path = tmp_path / "nonexistent.json"

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "unable to read file"

    def test_parse_flashcards_empty_cards_key(self, tmp_path):
        json_path = tmp_path / "flashcards.json"
        json_path.write_text(json.dumps({"cards": []}))

        assert NotebookLMClient("notebooklm").parse_flashcards(json_path) == []

    def test_parse_flashcards_nonexistent_path(self):
        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(
                Path("/nonexistent/file.json")
            )

        assert error.value.reason == "unable to read file"

    def test_create_flashcard_empty_front(self):
        result = NotebookLMClient("notebooklm")._create_flashcard({
            "front": "",
            "back": "answer",
        })
        assert result is None

    def test_create_flashcard_empty_back(self):
        result = NotebookLMClient("notebooklm")._create_flashcard({
            "front": "question",
            "back": "",
        })
        assert result is None

    def test_create_flashcard_both_empty(self):
        result = NotebookLMClient("notebooklm")._create_flashcard({
            "front": "",
            "back": "",
        })
        assert result is None

    def test_parse_flashcards_rejects_malformed_envelope(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text('[{"front": [], "back": "answer"}]')

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "card 0 has empty or non-string fields"

    def test_parse_flashcards_rejects_oversized_json(
        self, tmp_path, monkeypatch
    ):
        json_path = tmp_path / "cards.json"
        json_path.write_text('{"cards": []}')
        monkeypatch.setattr(
            NotebookLMClient, "MAX_JSON_BYTES", 5, raising=False
        )

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "JSON exceeds maximum size of 5 bytes"

    def test_parse_flashcards_rejects_too_many_cards(
        self, tmp_path, monkeypatch
    ):
        json_path = tmp_path / "cards.json"
        json_path.write_text(
            '{"cards": ['
            '{"front": "Question one", "back": "Answer one"},'
            '{"front": "Question two", "back": "Answer two"}'
            "]}"
        )
        monkeypatch.setattr(
            NotebookLMClient, "MAX_FLASHCARDS", 1, raising=False
        )

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "card count exceeds maximum of 1"

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_preserves_nested_identifier(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"notebook": {"id": "nb123"}}', stderr=""
        )

        assert (
            NotebookLMClient("notebooklm").create_notebook("test") == "nb123"
        )

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_rejects_empty_identifier(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"id": "  "}', stderr=""
        )

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").create_notebook("test")

        assert error.value.reason == "missing nonempty identifier"

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_rejects_oversized_utf8_response(
        self, mock_run, monkeypatch
    ):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"id":"ç"}', stderr=""
        )
        monkeypatch.setattr(NotebookLMClient, "MAX_JSON_BYTES", 10)

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").create_notebook("test")

        assert error.value.reason == "JSON exceeds maximum size of 10 bytes"

    @patch(
        "flashcards_generator.integrations.notebooklm.client.subprocess.run"
    )
    def test_create_notebook_rejects_invalid_nested_identifier(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"notebook": {"id": []}}', stderr=""
        )

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").create_notebook("test")

        assert error.value.reason == "missing nonempty identifier"

    def test_cards_key_wrong_type_does_not_fall_back_to_alias(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text(
            '{"cards": {}, "flashcards": '
            '[{"front": "question", "back": "answer"}]}'
        )

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "cards must be an array"

    def test_parse_flashcards_rejects_non_object_card(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text("[null]")

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "card 0 must be an object"

    def test_parse_flashcards_rejects_non_array_or_object(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text("42")

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "expected an array or object"

    def test_parse_flashcards_rejects_missing_cards_array(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text("{}")

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "missing cards array"

    def test_parse_flashcards_rejects_empty_card_string(self, tmp_path):
        json_path = tmp_path / "cards.json"
        json_path.write_text('{"cards": [{"front": " ", "back": "answer"}]}')

        with pytest.raises(NotebookLMResponseError) as error:
            NotebookLMClient("notebooklm").parse_flashcards(json_path)

        assert error.value.reason == "card 0 has empty or non-string fields"
