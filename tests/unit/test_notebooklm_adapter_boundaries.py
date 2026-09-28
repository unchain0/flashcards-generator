import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from flashcards_generator.domain_models.exceptions import (
    ArtifactDownloadError,
    NotebookLMResponseError,
)
from flashcards_generator.integrations.notebooklm.gateway import (
    NotebookLMAdapter,
)
from flashcards_generator.services.ports.flashcard_generator import (
    GenerationConfig,
)


def test_cancel_active_stops_tracked_command() -> None:
    adapter = NotebookLMAdapter("notebooklm")
    stop_process = Mock()
    adapter._process_runner._track_stopper(stop_process)

    adapter.cancel_active()

    stop_process.assert_called_once_with()


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_command_uses_profile_and_private_notebook_home(
    mock_popen: Mock, tmp_path: Path
) -> None:
    process = MagicMock(returncode=0)
    process.communicate.return_value = ("", "")
    mock_popen.return_value = process
    adapter = NotebookLMAdapter(
        "notebooklm", profile="personal", notebooklm_home=tmp_path
    )

    assert adapter._run_command(["list", "--json"])[0] == 0

    command = mock_popen.call_args.args[0]
    environment = mock_popen.call_args.kwargs["env"]
    assert command == [
        "notebooklm",
        "--profile",
        "personal",
        "list",
        "--json",
    ]
    assert environment["NOTEBOOKLM_HOME"] == str(tmp_path)
    assert environment["PATH"]


@patch(
    "flashcards_generator.integrations.notebooklm.process_runner.subprocess.Popen"
)
def test_download_translates_communication_failure_after_process_cleanup(
    mock_popen: Mock, tmp_path: Path
) -> None:
    process = MagicMock()
    process.stdout = Mock()
    process.stderr = Mock()
    failure = OSError("communication failed")
    process.communicate.side_effect = failure
    mock_popen.return_value = process
    adapter = NotebookLMAdapter("notebooklm")

    with (
        patch.object(adapter._process_runner, "_stop_process") as stop_process,
        pytest.raises(ArtifactDownloadError) as error,
    ):
        adapter.download_flashcards(
            "nb1", "artifact1", tmp_path / "cards.json"
        )

    assert error.value.artifact_id == "artifact1"
    assert error.value.__cause__ is failure
    stop_process.assert_called_once_with(process)
    process.stdout.close.assert_called_once_with()
    process.stderr.close.assert_called_once_with()
    assert not adapter._process_runner._active_stoppers


def test_non_cancellable_command_uses_no_cancellation_token() -> None:
    adapter = NotebookLMAdapter("notebooklm")

    assert adapter._process_runner._command_token(False, False) is None


def test_generation_retry_runs_second_attempt() -> None:
    adapter = NotebookLMAdapter("notebooklm")
    adapter._run_command = Mock(
        side_effect=[(1, "", "rate limit"), (0, "{}", "")]
    )

    with patch(
        "flashcards_generator.integrations.notebooklm.process_runner.time.sleep"
    ) as sleep:
        result = adapter._execute_with_retry(["generate"], timeout=12)

    assert result == (0, "{}", "")
    assert adapter._run_command.call_count == 2
    sleep.assert_called_once_with(300)


def test_download_wraps_process_errors() -> None:
    adapter = NotebookLMAdapter("notebooklm")
    adapter._run_command = Mock(side_effect=OSError("process unavailable"))

    with pytest.raises(ArtifactDownloadError) as error:
        adapter.download_flashcards("nb1", "artifact1", Path("out.json"))

    assert error.value.artifact_id == "artifact1"
    assert isinstance(error.value.__cause__, OSError)


def test_nested_identifier_is_supported() -> None:
    adapter = NotebookLMAdapter("notebooklm")

    assert (
        adapter._extract_identifier(
            {"notebook": {"id": "nested-id"}}, "create notebook", "notebook"
        )
        == "nested-id"
    )
    assert adapter._identifier_value({"id": "  "}) is None


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ("cards", "expected an array or object"),
        ({}, "missing cards array"),
    ],
)
def test_card_envelope_errors_remain_contextual(data, reason: str) -> None:
    with pytest.raises(NotebookLMResponseError) as error:
        NotebookLMAdapter("notebooklm")._extract_cards_data(data)

    assert error.value.reason == reason


def test_parse_flashcards_preserves_primary_field_precedence(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "cards.json"
    json_path.write_text(
        json.dumps({
            "cards": [
                {
                    "front": " Primary front ",
                    "question": "Alternative front",
                    "q": "Short front",
                    "back": " Primary back ",
                    "answer": "Alternative back",
                    "a": "Short back",
                }
            ],
            "flashcards": [
                {"front": "Alternative card", "back": "Alternative"}
            ],
        }),
        encoding="utf-8",
    )

    cards = NotebookLMAdapter("notebooklm").parse_flashcards(json_path)

    assert [(card.front, card.back) for card in cards] == [
        (" Primary front ", " Primary back ")
    ]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            '{"cards":"invalid","flashcards":[{"front":"fallback",'
            '"back":"fallback"}]}',
            "cards must be an array",
        ),
        (
            '{"cards":[{"front":null,"question":"fallback",'
            '"q":"fallback","back":"answer"}]}',
            "card 0 has empty or non-string fields",
        ),
        (
            '{"cards":[{"question":"","q":"fallback","back":"answer"}]}',
            "card 0 has empty or non-string fields",
        ),
        (
            '{"cards":[{"front":"question","back":null,"answer":'
            '"fallback","a":"fallback"}]}',
            "card 0 has empty or non-string fields",
        ),
        (
            '{"cards":[{"front":"question","answer":"","a":"fallback"}]}',
            "card 0 has empty or non-string fields",
        ),
    ],
)
def test_parse_flashcards_rejects_invalid_preferred_fields(
    tmp_path: Path, payload: str, reason: str
) -> None:
    json_path = tmp_path / "cards.json"
    json_path.write_text(payload, encoding="utf-8")

    with pytest.raises(NotebookLMResponseError) as error:
        NotebookLMAdapter("notebooklm").parse_flashcards(json_path)

    assert error.value.reason == reason


def test_parse_flashcards_enforces_file_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_path = tmp_path / "cards.json"
    json_path.write_text("[]")
    monkeypatch.setattr(NotebookLMAdapter, "MAX_JSON_BYTES", 1)

    with pytest.raises(NotebookLMResponseError) as error:
        NotebookLMAdapter("notebooklm").parse_flashcards(json_path)

    assert error.value.reason == "JSON exceeds maximum size of 1 bytes"


def test_parse_json_enforces_utf8_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(NotebookLMAdapter, "MAX_JSON_BYTES", 3)

    with pytest.raises(NotebookLMResponseError) as error:
        NotebookLMAdapter("notebooklm")._parse_json('"é"', "test")

    assert error.value.reason == "JSON exceeds maximum size of 3 bytes"


def test_parse_flashcards_enforces_card_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_path = tmp_path / "cards.json"
    json_path.write_text(json.dumps([{"front": "Q", "back": "A"}]))
    monkeypatch.setattr(NotebookLMAdapter, "MAX_FLASHCARDS", 0)

    with pytest.raises(NotebookLMResponseError) as error:
        NotebookLMAdapter("notebooklm").parse_flashcards(json_path)

    assert error.value.reason == "card count exceeds maximum of 0"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("[null]", "card 0 must be an object"),
        (
            '[{"front":"","back":"answer"}]',
            "card 0 has empty or non-string fields",
        ),
    ],
)
def test_parse_flashcards_rejects_invalid_items(
    tmp_path: Path, payload: str, reason: str
) -> None:
    json_path = tmp_path / "cards.json"
    json_path.write_text(payload)

    with pytest.raises(NotebookLMResponseError) as error:
        NotebookLMAdapter("notebooklm").parse_flashcards(json_path)

    assert error.value.reason == reason


def test_generate_flashcards_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NotebookLMAdapter("notebooklm")
    adapter._run_command = Mock(return_value=(0, '{"id":"x"}', ""))
    monkeypatch.setattr(NotebookLMAdapter, "MAX_JSON_BYTES", 5)

    assert adapter.generate_flashcards("nb1", GenerationConfig()) is None
