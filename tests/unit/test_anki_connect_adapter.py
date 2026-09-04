"""Unit tests for the AnkiConnect protocol adapter."""

from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest

from flashcards_generator.adapters.anki_connect_adapter import (
    AnkiConnectAdapter,
)
from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.domain.exceptions import AnkiConnectError


def _response_for(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if payload["action"] == "createDeck":
        return httpx.Response(200, json={"result": 1, "error": None})
    return httpx.Response(200, json={"result": [42], "error": None})


def test_export_sends_cloze_note_to_configured_deck() -> None:
    client = httpx.Client(transport=httpx.MockTransport(_response_for))
    adapter = AnkiConnectAdapter(
        deck_name="Estácio::Disciplina::Unidade 1",
        url="http://anki.test",
    )
    adapter._create_client = Mock(return_value=client)

    imported = adapter.export(
        Deck(
            name="source",
            flashcards=[
                Flashcard(
                    front="A célula contém {{c1::mitocôndrias}}.",
                    back="Respiração celular.",
                    tags=["biologia"],
                )
            ],
        )
    )

    assert imported == 1


def test_export_rejects_per_note_failure() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"result": [None], "error": None}
            )
        )
    )
    adapter = AnkiConnectAdapter("Estácio::Disciplina")
    adapter._create_client = Mock(return_value=client)

    with pytest.raises(AnkiConnectError, match="addNotes"):
        adapter.export(
            Deck(
                name="source",
                flashcards=[
                    Flashcard(front="{{c1::termo}}", back="definição")
                ],
            )
        )


def test_export_rejects_invalid_response_shape() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"result": 1})
        )
    )
    adapter = AnkiConnectAdapter("Estácio::Disciplina")
    adapter._create_client = Mock(return_value=client)

    with pytest.raises(AnkiConnectError, match="result and error"):
        adapter.export(Deck(name="source"))
