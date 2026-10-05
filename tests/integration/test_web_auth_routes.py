from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flashcards_generator.delivery.web.app import create_app
from flashcards_generator.delivery.web.auth import SESSION_COOKIE_NAME
from flashcards_generator.delivery.web.config import WebSettings
from flashcards_generator.integrations.web_models import WebSessionRecord

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
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        bootstrap_password=SecretStr("test-password-123"),
        auto_create_schema=True,
    )
    app = create_app(settings)
    async with AsyncTestClient(
        app, base_url="http://test.local"
    ) as test_client:
        yield test_client


@pytest.mark.parametrize("body", [b"{", b"{}", b"[]", b'{"password": 7}'])
async def test_login_rejects_invalid_or_missing_password_payloads(
    client: AsyncTestClient[Litestar], body: bytes
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Informe a senha"
    assert "set-cookie" not in response.headers
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with client.app.state.database.sessions() as session:
        assert await session.scalar(select(WebSessionRecord)) is None


async def test_login_rejects_an_invalid_password_without_creating_a_session(
    client: AsyncTestClient[Litestar],
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"password": "incorrect-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Senha inválida"
    assert "incorrect-password" not in response.text
    assert "set-cookie" not in response.headers
    async with client.app.state.database.sessions() as session:
        assert await session.scalar(select(WebSessionRecord)) is None


async def test_companion_token_is_short_lived_and_revoked_with_web_session(
    client: AsyncTestClient[Litestar],
) -> None:
    page_response = await client.get("/api/v1/auth/me")
    assert (
        await client.post("/api/v1/auth/companion/token")
    ).status_code == 401
    await client.post(
        "/api/v1/auth/login", json={"password": "test-password-123"}
    )

    issued = await client.post("/api/v1/auth/companion/token")
    capability = issued.json()["access_token"]
    verified = await client.post(
        "/api/v1/auth/companion/verify",
        json={"access_token": capability},
    )
    rejected = await client.post(
        "/api/v1/auth/companion/verify",
        json={"access_token": "invalid"},
    )
    await client.post("/api/v1/auth/logout")
    revoked = await client.post(
        "/api/v1/auth/companion/verify",
        json={"access_token": capability},
    )

    assert issued.status_code == 201
    assert (
        "http://127.0.0.1:8766"
        in page_response.headers["content-security-policy"]
    )
    assert issued.json()["expires_in"] == 300
    assert set(issued.json()) == {"access_token", "expires_in"}
    assert verified.status_code == 201
    assert len(verified.json()["subject"]) == 32
    assert rejected.status_code == 401
    assert revoked.status_code == 401


async def test_companion_token_rejects_a_session_revoked_after_auth_guard(
    client: AsyncTestClient[Litestar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await client.post(
        "/api/v1/auth/login", json={"password": "test-password-123"}
    )

    async def revoked_session(
        _session: AsyncSession, _token: str | None
    ) -> str | None:
        return None

    monkeypatch.setattr(
        client.app.state.database.auth,
        "create_companion_token",
        revoked_session,
    )

    response = await client.post("/api/v1/auth/companion/token")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


async def test_logout_revokes_only_its_session_and_expires_the_cookie(
    client: AsyncTestClient[Litestar],
) -> None:
    database = client.app.state.database
    async with database.sessions() as session:
        other_user_id = await database.auth.create_user(
            session, "other-user-password"
        )
    primary = await client.post(
        "/api/v1/auth/login", json={"password": "test-password-123"}
    )
    other = await client.post(
        "/api/v1/auth/login", json={"password": "other-user-password"}
    )
    primary_token = primary.cookies.get(SESSION_COOKIE_NAME)
    other_token = other.cookies.get(SESSION_COOKIE_NAME)
    assert primary_token is not None
    assert other_token is not None
    primary_cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={primary_token}"}
    other_cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={other_token}"}

    assert (
        await client.get("/api/v1/auth/me", headers=primary_cookie)
    ).status_code == 200
    logout = await client.post("/api/v1/auth/logout", headers=primary_cookie)
    repeated_logout = await client.post(
        "/api/v1/auth/logout", headers=primary_cookie
    )
    revoked = await client.get("/api/v1/auth/me", headers=primary_cookie)
    still_valid = await client.get("/api/v1/auth/me", headers=other_cookie)
    async with database.sessions() as session:
        remaining = (await session.scalars(select(WebSessionRecord))).all()

    assert logout.status_code == 200
    assert logout.json() == {"authenticated": False}
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert repeated_logout.status_code == 200
    assert repeated_logout.json() == {"authenticated": False}
    assert revoked.status_code == 401
    assert still_valid.status_code == 200
    assert len(remaining) == 1
    assert remaining[0].user_id == other_user_id


@pytest.mark.parametrize(
    "cookie",
    [None, f"{SESSION_COOKIE_NAME}=unknown-token"],
)
async def test_logout_is_idempotent_without_a_valid_session(
    client: AsyncTestClient[Litestar], cookie: str | None
) -> None:
    headers = {"Cookie": cookie} if cookie is not None else {}

    response = await client.post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert "Max-Age=0" in response.headers["set-cookie"]
    async with client.app.state.database.sessions() as session:
        assert await session.scalar(select(WebSessionRecord)) is None
