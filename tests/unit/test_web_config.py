from __future__ import annotations

import os
from typing import Literal

import pytest
from pydantic import SecretStr, ValidationError

from flashcards_generator.delivery.web.config import WebSettings


@pytest.fixture(autouse=True)
def isolate_flashcards_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("FLASHCARDS_"):
            monkeypatch.delenv(name)


@pytest.mark.parametrize(
    ("field", "environment_name"),
    [
        ("session_secret", "FLASHCARDS_SESSION_SECRET"),
        ("auth_lookup_secret", "FLASHCARDS_AUTH_LOOKUP_SECRET"),
    ],
)
@pytest.mark.parametrize("length", [0, 31])
def test_production_rejects_each_secret_below_32_characters(
    field: Literal["session_secret", "auth_lookup_secret"],
    environment_name: str,
    length: int,
) -> None:
    secrets = {
        "session_secret": SecretStr("s" * 32),
        "auth_lookup_secret": SecretStr("l" * 32),
    }
    secrets[field] = SecretStr("x" * length)

    with pytest.raises(ValidationError) as error:
        WebSettings(
            environment="production",
            database_url="postgresql+asyncpg://test:test@localhost/flashcards",
            session_secret=secrets["session_secret"],
            auth_lookup_secret=secrets["auth_lookup_secret"],
            auto_create_schema=False,
            _env_file=None,
        )

    assert environment_name in str(error.value)
    assert "at least 32 characters" in str(error.value)


def test_production_accepts_secrets_at_exactly_32_characters() -> None:
    session_secret = "s" * 32
    lookup_secret = "l" * 32

    settings = WebSettings(
        environment="production",
        database_url="postgresql+asyncpg://test:test@localhost/flashcards",
        session_secret=SecretStr(session_secret),
        auth_lookup_secret=SecretStr(lookup_secret),
        auto_create_schema=False,
        _env_file=None,
    )

    assert settings.session_secret.get_secret_value() == session_secret
    assert settings.auth_lookup_secret.get_secret_value() == lookup_secret


def test_production_rejects_reused_secrets() -> None:
    with pytest.raises(ValidationError, match="must differ in production"):
        WebSettings(
            environment="production",
            database_url="postgresql+asyncpg://test:test@localhost/flashcards",
            session_secret=SecretStr("s" * 32),
            auth_lookup_secret=SecretStr("s" * 32),
            auto_create_schema=False,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///:memory:",
        "postgresql://test:test@localhost/flashcards",
        "postgresql+psycopg://test:test@localhost/flashcards",
        "mysql+asyncmy://test:test@localhost/flashcards",
        "postgresql+asyncpg-invalid://test:test@localhost/flashcards",
        "postgresql+asyncpg",
        "",
    ],
)
def test_production_rejects_unsupported_database_drivers(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError, match="FLASHCARDS_DATABASE_URL"):
        WebSettings(
            environment="production",
            database_url=database_url,
            session_secret=SecretStr("s" * 32),
            auth_lookup_secret=SecretStr("l" * 32),
            auto_create_schema=False,
            _env_file=None,
        )


def test_production_rejects_the_default_database() -> None:
    with pytest.raises(ValidationError, match="FLASHCARDS_DATABASE_URL"):
        WebSettings(
            environment="production",
            session_secret=SecretStr("s" * 32),
            auth_lookup_secret=SecretStr("l" * 32),
            auto_create_schema=False,
            _env_file=None,
        )
