from __future__ import annotations

import json

from flashcards_generator.domain_models.entities import Flashcard
from flashcards_generator.domain_models.exceptions import (
    NotebookLMResponseError,
)

type JSONValue = (
    str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
)


def parse_json(stdout: str, operation: str, max_json_bytes: int) -> JSONValue:
    if len(stdout.encode("utf-8")) > max_json_bytes:
        raise NotebookLMResponseError(
            operation,
            f"JSON exceeds maximum size of {max_json_bytes} bytes",
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as error:
        raise NotebookLMResponseError(operation, "invalid JSON") from error


def extract_identifier(
    data: JSONValue, operation: str, keys: tuple[str, ...]
) -> str:
    if not isinstance(data, dict):
        raise NotebookLMResponseError(operation, "expected an object response")
    for key in keys:
        identifier = identifier_value(data.get(key))
        if identifier is not None:
            return identifier
    raise NotebookLMResponseError(operation, "missing nonempty identifier")


def identifier_value(value: JSONValue) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and value.strip() else None


def extract_cards_data(
    data: JSONValue, max_flashcards: int
) -> list[JSONValue]:
    if isinstance(data, list):
        return _validated_card_array(data, "cards", max_flashcards)
    if not isinstance(data, dict):
        raise NotebookLMResponseError(
            "parse flashcards", "expected an array or object"
        )
    for key in ("cards", "flashcards"):
        if key in data:
            return _validated_card_array(data[key], key, max_flashcards)
    raise NotebookLMResponseError("parse flashcards", "missing cards array")


def create_flashcard(item: dict[str, JSONValue]) -> Flashcard | None:
    front = item.get("front", item.get("question", item.get("q", "")))
    back = item.get("back", item.get("answer", item.get("a", "")))
    if (
        isinstance(front, str)
        and front.strip()
        and isinstance(back, str)
        and back.strip()
    ):
        return Flashcard(front=front, back=back)
    return None


def parse_flashcard_item(item: JSONValue, index: int) -> Flashcard:
    if not isinstance(item, dict):
        raise NotebookLMResponseError(
            "parse flashcards", f"card {index} must be an object"
        )
    card = create_flashcard(item)
    if card is None:
        raise NotebookLMResponseError(
            "parse flashcards",
            f"card {index} has empty or non-string fields",
        )
    return card


def _validated_card_array(
    cards: JSONValue, key: str, max_flashcards: int
) -> list[JSONValue]:
    if not isinstance(cards, list):
        raise NotebookLMResponseError(
            "parse flashcards", f"{key} must be an array"
        )
    if len(cards) > max_flashcards:
        raise NotebookLMResponseError(
            "parse flashcards",
            f"card count exceeds maximum of {max_flashcards}",
        )
    return cards
