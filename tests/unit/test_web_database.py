from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from flashcards_generator.integrations import web_database
from flashcards_generator.integrations.web_database import WebDatabase
from flashcards_generator.integrations.web_models import WebBase

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_database_schema_persists_only_authentication_state() -> None:
    assert set(WebBase.metadata.tables) == {"web_users", "web_sessions"}


async def test_postgresql_database_uses_connection_pool_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine_factory = create_async_engine
    calls: list[tuple[str, dict[str, int | bool]]] = []

    def create_test_engine(url: str, **options: int | bool) -> AsyncEngine:
        calls.append((url, options))
        return engine_factory(
            f"sqlite+aiosqlite:///{tmp_path / 'postgres-settings.db'}"
        )

    monkeypatch.setattr(
        web_database, "create_async_engine", create_test_engine
    )
    database_url = "postgresql+asyncpg://test:test@localhost/flashcards"
    database = WebDatabase(
        database_url=database_url,
        session_secret="s" * 32,
        lookup_secret="l" * 32,
        bootstrap_password=None,
        auto_create_schema=False,
    )

    try:
        assert calls == [
            (
                database_url,
                {
                    "pool_pre_ping": True,
                    "pool_size": 5,
                    "max_overflow": 10,
                },
            )
        ]
    finally:
        await database.close()


async def test_start_propagates_database_errors_with_schema_creation_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = WebDatabase(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}",
        session_secret="s" * 32,
        lookup_secret="l" * 32,
        bootstrap_password=None,
        auto_create_schema=True,
    )

    async def fail_bootstrap(
        _session: AsyncSession, _password: str | None
    ) -> None:
        raise DBAPIError("bootstrap", {}, RuntimeError("synthetic failure"))

    monkeypatch.setattr(database.auth, "ensure_bootstrap", fail_bootstrap)

    try:
        with pytest.raises(DBAPIError, match="synthetic failure"):
            await database.start()
    finally:
        await database.close()
