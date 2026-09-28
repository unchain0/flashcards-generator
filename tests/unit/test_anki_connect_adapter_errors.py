from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.domain_models.exceptions import AnkiConnectError
from flashcards_generator.integrations.anki.connect import (
    AnkiConnectAdapter,
)


@pytest.mark.parametrize(
    ("deck_name", "url", "timeout_seconds", "message"),
    [
        (" ", "http://anki.test", 1, "deck_name must not be empty"),
        ("Deck", " ", 1, "url must not be empty"),
        ("Deck", "http://anki.test", 0, "timeout_seconds must be positive"),
    ],
)
def test_invalid_adapter_configuration_is_rejected(
    deck_name: str, url: str, timeout_seconds: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        AnkiConnectAdapter(deck_name, url, timeout_seconds=timeout_seconds)


def test_create_client_uses_bounded_transport() -> None:
    adapter = AnkiConnectAdapter("Deck", timeout_seconds=2.5)
    with (
        patch(
            "flashcards_generator.integrations.anki.connect.httpx.HTTPTransport"
        ) as transport,
        patch(
            "flashcards_generator.integrations.anki.connect.httpx.Client"
        ) as client,
    ):
        result = adapter._create_client()

    limits = transport.call_args.kwargs["limits"]
    assert limits.max_connections == 200
    assert limits.max_keepalive_connections == 40
    assert limits.keepalive_expiry == 30.0
    assert transport.call_args.kwargs["retries"] == 3
    assert client.call_args.kwargs["timeout"].connect == 2.5
    assert client.call_args.kwargs["transport"] is transport.return_value
    assert result is client.return_value


def test_empty_export_still_creates_deck_without_notes() -> None:
    client_context = MagicMock()
    client = client_context.__enter__.return_value
    adapter = AnkiConnectAdapter("Deck")
    adapter._create_client = Mock(return_value=client_context)
    adapter._invoke = Mock(return_value=1)

    assert adapter.export(Deck(name="source")) == 0

    adapter._invoke.assert_called_once_with(
        client, "createDeck", {"deck": "Deck"}
    )


def test_post_includes_configured_api_key() -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: (
            requests.append(request)
            or httpx.Response(200, json={"result": 1, "error": None})
        )
    )
    adapter = AnkiConnectAdapter(
        "Deck", "http://anki.test", api_key="local-key"
    )

    with httpx.Client(transport=transport) as client:
        adapter._post(client, "createDeck", {"deck": "Deck"})

    assert requests[0].headers["content-type"] == "application/json"
    assert b'"key":"local-key"' in requests[0].content


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ReadTimeout("timed out"), "request timed out"),
        (httpx.ConnectError("connection refused"), "connection refused"),
    ],
)
def test_post_translates_httpx_failures(
    failure: Exception, reason: str
) -> None:
    adapter = AnkiConnectAdapter("Deck")
    client = Mock(post=Mock(side_effect=failure))

    with pytest.raises(AnkiConnectError) as error:
        adapter._post(client, "createDeck", {})

    assert error.value.operation == "createDeck"
    assert error.value.reason == reason
    assert error.value.__cause__ is failure


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(200, json=[]), "response must be an object"),
        (
            httpx.Response(200, content=b"not json"),
            "response was not valid JSON",
        ),
        (
            httpx.Response(200, json={"result": None, "error": 7}),
            "response error must be a string or null",
        ),
    ],
)
def test_response_parser_rejects_invalid_shapes(
    response: httpx.Response, reason: str
) -> None:
    with pytest.raises(AnkiConnectError) as error:
        AnkiConnectAdapter._parse_response(response, "createDeck")

    assert error.value.reason == reason


def test_response_parser_preserves_anki_error_text() -> None:
    response = httpx.Response(
        200, json={"result": None, "error": "deck unavailable"}
    )

    with pytest.raises(AnkiConnectError) as error:
        AnkiConnectAdapter._parse_response(response, "createDeck")

    assert error.value.reason == "deck unavailable"


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        ("not-an-array", "result must be an array"),
        ([True], "result contained a non-numeric note ID"),
        ([42], "returned 1 results for 2 notes"),
    ],
)
def test_note_id_validation_rejects_invalid_results(
    result: object, reason: str
) -> None:
    with pytest.raises(AnkiConnectError) as error:
        AnkiConnectAdapter._parse_note_ids(result, expected_count=2)

    assert error.value.reason == reason
