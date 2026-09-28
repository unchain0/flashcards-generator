from __future__ import annotations

import pytest
from pydantic import ValidationError

from flashcards_generator.delivery.companion.config import CompanionSettings


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://flashcards.example.com/", "https://flashcards.example.com"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("https://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
    ],
)
def test_web_origin_is_normalized(origin: str, expected: str) -> None:
    assert CompanionSettings(web_origin=origin).web_origin == expected


@pytest.mark.parametrize(
    "origin",
    [
        "https://",
        "http://flashcards.example.com",
        "ftp://flashcards.example.com",
        "https://flashcards.example.com/path",
        "https://user@flashcards.example.com",
        "https://flashcards.example.com?debug=true",
    ],
)
def test_web_origin_rejects_non_exact_or_insecure_origins(
    origin: str,
) -> None:
    with pytest.raises(ValidationError):
        CompanionSettings(web_origin=origin)
