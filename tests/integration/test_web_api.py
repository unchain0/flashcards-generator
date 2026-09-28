from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from flashcards_generator.delivery.web.app import create_app
from flashcards_generator.delivery.web.config import WebSettings
from flashcards_generator.integrations.web_models import (
    WebBase,
    WebSessionRecord,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(
    tmp_path: Path,
) -> AsyncIterator[AsyncTestClient[Litestar]]:
    settings = WebSettings(
        environment="test",
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
        bootstrap_password=SecretStr("test-password-123"),
        auto_create_schema=True,
    )
    app = create_app(settings)
    async with AsyncTestClient(
        app, base_url="http://test.local"
    ) as test_client:
        yield test_client


async def test_live_health_is_public(
    client: AsyncTestClient[Litestar],
) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    assert all(
        value in response.headers.get(name, "")
        for name, value in (
            ("content-security-policy", "script-src 'self'"),
            ("x-content-type-options", "nosniff"),
            ("x-frame-options", "DENY"),
            ("referrer-policy", "no-referrer"),
        )
    )


async def test_dashboard_is_served_without_authentication(
    client: AsyncTestClient[Litestar],
) -> None:
    dashboard = await client.get("/")
    assert dashboard.status_code == 200
    assert "Gerador de flashcards" in dashboard.text


async def test_readiness_requires_the_authentication_schema(
    tmp_path: Path,
) -> None:
    settings = WebSettings(
        environment="test",
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'unmigrated.db'}",
        auto_create_schema=False,
    )
    app = create_app(settings)
    async with AsyncTestClient(
        app, base_url="http://test.local"
    ) as test_client:
        response = await test_client.get("/health/ready")
        live = await test_client.get("/health/live")

    assert response.status_code == 503
    assert live.status_code == 200


@pytest.mark.parametrize(
    ("missing_table", "missing_column"),
    [
        ("web_users", None),
        ("web_sessions", None),
        (None, ("web_users", "password_hash")),
        (None, ("web_sessions", "expires_at")),
    ],
)
async def test_readiness_rejects_missing_required_schema(
    tmp_path: Path,
    missing_table: str | None,
    missing_column: tuple[str, str] | None,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'partial.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        tables = [
            table
            for table in WebBase.metadata.sorted_tables
            if table.name != missing_table
        ]
        await connection.run_sync(
            lambda sync_connection: WebBase.metadata.create_all(
                sync_connection, tables=tables
            )
        )
        if missing_column is not None:
            table_name, column_name = missing_column
            if table_name == WebSessionRecord.__tablename__:
                await connection.exec_driver_sql(
                    "DROP INDEX ix_web_sessions_expires_at"
                )
            await connection.exec_driver_sql(
                f"ALTER TABLE {table_name} DROP COLUMN {column_name}"
            )
    await engine.dispose()

    app = create_app(
        WebSettings(
            environment="test",
            data_dir=tmp_path / "data",
            database_url=database_url,
            auto_create_schema=False,
        )
    )
    async with AsyncTestClient(
        app, base_url="http://test.local"
    ) as test_client:
        response = await test_client.get("/health/ready")
        live = await test_client.get("/health/live")

    assert response.status_code == 503
    assert live.status_code == 200


async def test_readiness_accepts_the_complete_schema(
    client: AsyncTestClient[Litestar],
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200


async def test_password_login_sets_a_session_cookie(
    client: AsyncTestClient[Litestar],
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"password": "test-password-123"}
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert "flashcards_session=" in response.headers["set-cookie"]
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200


def test_production_requires_authentication_secrets(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WebSettings(
            environment="production",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
        )


def test_production_requires_explicit_migrations(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WebSettings(
            environment="production",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
            session_secret=SecretStr("s" * 32),
            auth_lookup_secret=SecretStr("l" * 32),
            auto_create_schema=True,
        )


def test_empty_bootstrap_password_is_treated_as_unset() -> None:
    settings = WebSettings(
        environment="test",
        bootstrap_password=SecretStr(""),
    )

    assert settings.bootstrap_password is None


def test_production_accepts_explicit_persistent_secrets(
    tmp_path: Path,
) -> None:
    settings = WebSettings(
        environment="production",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
        session_secret=SecretStr("s" * 32),
        auth_lookup_secret=SecretStr("l" * 32),
        auto_create_schema=False,
    )

    assert settings.environment == "production"


def test_dashboard_renderer_keeps_uploaded_content_as_text() -> None:
    source = (
        Path(__file__).parents[2] / "frontend/src/interfaces/browser_view.ts"
    ).read_text()

    assert "innerHTML" not in source
    assert "localStorage" not in source
    assert "textContent" in source
