"""AnkiConnect adapter for direct local deck imports."""

from __future__ import annotations

from typing import Final

import httpx

from flashcards_generator.application.math_processor import (
    convert_to_anki_math_format,
)
from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.domain.exceptions import AnkiConnectError
from flashcards_generator.domain.ports.anki_exporter import AnkiExporterPort

DEFAULT_ANKI_CONNECT_URL: Final = "http://127.0.0.1:8765"
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 10.0
ANKI_CONNECT_VERSION: Final = 6
MAX_CONNECTIONS: Final = 200
MAX_KEEPALIVE_CONNECTIONS: Final = 40
KEEPALIVE_EXPIRY_SECONDS: Final = 30.0
TRANSPORT_RETRIES: Final = 3


class AnkiConnectAdapter(AnkiExporterPort):
    """Send generated Cloze notes to a local AnkiConnect endpoint."""

    def __init__(
        self,
        deck_name: str,
        url: str = DEFAULT_ANKI_CONNECT_URL,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not deck_name.strip():
            raise ValueError("deck_name must not be empty")
        if not url.strip():
            raise ValueError("url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self.deck_name = deck_name
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def export(self, deck: Deck) -> int:
        """Ensure the target deck and import all generated Cloze notes."""
        with self._create_client() as client:
            self._invoke(
                client,
                "createDeck",
                {"deck": self.deck_name},
            )
            if not deck.flashcards:
                return 0

            result = self._invoke(
                client,
                "addNotes",
                {"notes": self._build_notes(deck.flashcards)},
            )

        return self._parse_note_ids(result, len(deck.flashcards))

    def _create_client(self) -> httpx.Client:
        limits = httpx.Limits(
            max_connections=MAX_CONNECTIONS,
            max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
        )
        transport = httpx.HTTPTransport(
            retries=TRANSPORT_RETRIES,
            limits=limits,
        )
        return httpx.Client(
            timeout=httpx.Timeout(self.timeout_seconds),
            transport=transport,
        )

    def _invoke(
        self,
        client: httpx.Client,
        operation: str,
        params: dict[str, object],
    ) -> object:
        response = self._post(client, operation, params)
        return self._parse_response(response, operation)

    def _post(
        self,
        client: httpx.Client,
        operation: str,
        params: dict[str, object],
    ) -> httpx.Response:
        request: dict[str, object] = {
            "action": operation,
            "version": ANKI_CONNECT_VERSION,
            "params": params,
        }
        if self.api_key is not None:
            request["key"] = self.api_key

        try:
            response = client.post(self.url, json=request)
            response.raise_for_status()
        except httpx.TimeoutException as error:
            raise AnkiConnectError(operation, "request timed out") from error
        except httpx.HTTPError as error:
            raise AnkiConnectError(operation, str(error)) from error
        return response

    @staticmethod
    def _parse_response(response: httpx.Response, operation: str) -> object:
        payload = AnkiConnectAdapter._decode_response(response, operation)
        if not isinstance(payload, dict):
            raise AnkiConnectError(operation, "response must be an object")
        return AnkiConnectAdapter._extract_result(payload, operation)

    @staticmethod
    def _decode_response(response: httpx.Response, operation: str) -> object:
        try:
            return response.json()
        except ValueError as error:
            raise AnkiConnectError(
                operation, "response was not valid JSON"
            ) from error

    @staticmethod
    def _extract_result(
        payload: dict[object, object], operation: str
    ) -> object:
        if "result" not in payload or "error" not in payload:
            raise AnkiConnectError(
                operation, "response must contain result and error"
            )

        response_error = payload["error"]
        if response_error is not None:
            raise AnkiConnectError(
                operation, AnkiConnectAdapter._error_reason(response_error)
            )
        return payload["result"]

    @staticmethod
    def _error_reason(response_error: object) -> str:
        if isinstance(response_error, str):
            return response_error
        return "response error must be a string or null"

    def _build_notes(
        self, flashcards: list[Flashcard]
    ) -> list[dict[str, object]]:
        return [
            {
                "deckName": self.deck_name,
                "modelName": "Cloze",
                "fields": {
                    "Text": convert_to_anki_math_format(card.front),
                    "Extra": convert_to_anki_math_format(card.back),
                },
                "tags": list(card.tags),
                "options": {
                    "allowDuplicate": False,
                    "duplicateScope": "deck",
                    "duplicateScopeDeckName": self.deck_name,
                },
            }
            for card in flashcards
        ]

    @staticmethod
    def _parse_note_ids(result: object, expected_count: int) -> int:
        note_ids = AnkiConnectAdapter._require_note_ids(result)
        AnkiConnectAdapter._validate_note_count(note_ids, expected_count)
        failures = sum(note_id is None for note_id in note_ids)
        if failures:
            raise AnkiConnectError(
                "addNotes", f"{failures} note(s) were not imported"
            )
        return len(note_ids)

    @staticmethod
    def _require_note_ids(result: object) -> list[object]:
        if not isinstance(result, list):
            raise AnkiConnectError("addNotes", "result must be an array")
        for note_id in result:
            if note_id is not None and (
                not isinstance(note_id, int) or isinstance(note_id, bool)
            ):
                raise AnkiConnectError(
                    "addNotes", "result contained a non-numeric note ID"
                )
        return result

    @staticmethod
    def _validate_note_count(
        note_ids: list[object], expected_count: int
    ) -> None:
        if len(note_ids) != expected_count:
            raise AnkiConnectError(
                "addNotes",
                f"returned {len(note_ids)} results for {expected_count} notes",
            )
