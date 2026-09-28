from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import pytest
from pydantic import JsonValue, TypeAdapter

from flashcards_generator.domain_models.exceptions import AnkiConnectError
from flashcards_generator.services.dto.workflow import AnkiExportOptions
from tests.integration.anki_workflow_support import (
    _AnkiEndpoint,
    _generate,
    _provide_anki_endpoint,
    _scenario,
)

pytestmark = pytest.mark.integration


class _AnkiNote(TypedDict):
    deckName: str
    modelName: str
    fields: dict[str, str]
    tags: list[str]
    options: dict[str, JsonValue]


_ANKI_NOTES_ADAPTER = TypeAdapter(list[_AnkiNote])


@pytest.fixture
def anki_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_AnkiEndpoint]:
    yield from _provide_anki_endpoint(monkeypatch)


def test_composed_workflow_generates_csv_and_imports_cloze_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anki_endpoint: _AnkiEndpoint,
) -> None:
    scenario = _scenario(tmp_path, monkeypatch)

    outcome = _generate(scenario)
    imported = scenario.workflows.export_to_anki(
        outcome.decks,
        AnkiExportOptions(
            deck_name="Estácio::Disciplina::Unidade 1",
            url=anki_endpoint.url,
        ),
    )

    csv_path = scenario.request.output_dir / "Unidade 1" / "Aula.csv"
    with csv_path.open(encoding="utf-8", newline="") as file_obj:
        rows = list(csv.reader(file_obj))
    assert outcome.discovered_sources == 1
    assert outcome.completed_sources == 1
    assert outcome.csv_paths == (csv_path,)
    assert rows == [["O núcleo contém {{c1::DNA}}.", "Material genético."]]
    assert imported == 1
    assert [request["action"] for request in anki_endpoint.state.requests] == [
        "createDeck",
        "addNotes",
    ]
    assert anki_endpoint.state.attempted_urls == [
        anki_endpoint.url,
        anki_endpoint.url,
    ]
    note_payload = anki_endpoint.state.requests[1]["params"]["notes"]
    assert isinstance(note_payload, list)
    notes = _ANKI_NOTES_ADAPTER.validate_python(note_payload)
    assert notes == [
        {
            "deckName": "Estácio::Disciplina::Unidade 1",
            "modelName": "Cloze",
            "fields": {
                "Text": "O núcleo contém {{c1::DNA}}.",
                "Extra": "Material genético.",
            },
            "tags": ["unidade_1_aula"],
            "options": {
                "allowDuplicate": False,
                "duplicateScope": "deck",
                "duplicateScopeDeckName": "Estácio::Disciplina::Unidade 1",
            },
        }
    ]


def test_anki_failure_preserves_generated_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anki_endpoint: _AnkiEndpoint,
) -> None:
    anki_endpoint.state.reject_notes = True
    scenario = _scenario(tmp_path, monkeypatch)
    outcome = _generate(scenario)
    options = AnkiExportOptions(
        deck_name="Estácio::Disciplina",
        url=anki_endpoint.url,
    )
    csv_path = scenario.request.output_dir / "Unidade 1" / "Aula.csv"
    csv_before_export = (
        '"O núcleo contém {{c1::DNA}}.","Material genético."\r\n'
    ).encode()
    assert csv_path.read_bytes() == csv_before_export

    with pytest.raises(AnkiConnectError, match="controlled import failure"):
        scenario.workflows.export_to_anki(outcome.decks, options)

    assert csv_path.is_file()
    assert csv_path.read_bytes() == csv_before_export
    assert [request["action"] for request in anki_endpoint.state.requests] == [
        "createDeck",
        "addNotes",
    ]
    assert anki_endpoint.state.attempted_urls == [
        anki_endpoint.url,
        anki_endpoint.url,
    ]


def test_generation_saves_csv_without_an_anki_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anki_endpoint: _AnkiEndpoint,
) -> None:
    scenario = _scenario(tmp_path, monkeypatch)

    outcome = _generate(scenario)

    csv_path = scenario.request.output_dir / "Unidade 1" / "Aula.csv"
    assert outcome.csv_paths == (csv_path,)
    assert csv_path.read_text(encoding="utf-8") == (
        '"O núcleo contém {{c1::DNA}}.","Material genético."\n'
    )
    assert anki_endpoint.state.attempted_urls == []
    assert anki_endpoint.state.requests == []
