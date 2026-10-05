from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from flashcards_generator.delivery.companion.app import create_app
from flashcards_generator.delivery.companion.config import CompanionSettings
from flashcards_generator.integrations.notebooklm.management import (
    NotebookLMManagement,
)
from flashcards_generator.services.dto.workflow import AuthStatus

pytestmark = pytest.mark.anyio

ORIGIN = "https://flashcards.example.com"
USER_ID = "a" * 32


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncTestClient[Litestar]]:
    monkeypatch.setattr(
        NotebookLMManagement,
        "auth_status",
        lambda _manager: AuthStatus(False, "login required"),
    )
    monkeypatch.setattr(
        NotebookLMManagement,
        "login",
        lambda _manager: AuthStatus(True, "authenticated"),
    )

    async def verify(token: str, _settings: CompanionSettings) -> str | None:
        if token == "wrong-subject":
            return "not-a-profile-id"
        return USER_ID if token == "valid-capability" else None

    settings = CompanionSettings(web_origin=ORIGIN, data_dir=tmp_path)
    app = create_app(settings, token_verifier=verify)
    async with AsyncTestClient(
        app, base_url="http://127.0.0.1:8766"
    ) as test_client:
        yield test_client


async def test_status_and_login_use_the_user_local_profile(
    client: AsyncTestClient[Litestar],
) -> None:
    headers = {
        "Origin": ORIGIN,
        "Authorization": "Bearer valid-capability",
    }

    status = await client.get("/v1/notebooklm/status", headers=headers)
    login = await client.post("/v1/notebooklm/login", headers=headers)

    assert status.status_code == 200
    assert status.json() == {
        "authenticated": False,
        "status": "login_required",
        "message": "login required",
    }
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert (client.app.state.settings.data_dir / "profiles" / USER_ID).is_dir()


async def test_companion_rejects_missing_token_invalid_token_and_wrong_origin(
    client: AsyncTestClient[Litestar],
) -> None:
    no_token = await client.get(
        "/v1/notebooklm/status", headers={"Origin": ORIGIN}
    )
    invalid_token = await client.get(
        "/v1/notebooklm/status",
        headers={"Origin": ORIGIN, "Authorization": "Bearer invalid"},
    )
    wrong_origin = await client.get(
        "/v1/notebooklm/status",
        headers={
            "Origin": "https://attacker.example",
            "Authorization": "Bearer valid-capability",
        },
    )
    invalid_subject = await client.get(
        "/v1/notebooklm/status",
        headers={"Origin": ORIGIN, "Authorization": "Bearer wrong-subject"},
    )

    assert no_token.status_code == 401
    assert invalid_token.status_code == 401
    assert wrong_origin.status_code == 401
    assert invalid_subject.status_code == 401


async def test_companion_rejects_bearer_with_embedded_whitespace(
    client: AsyncTestClient[Litestar],
) -> None:
    response = await client.get(
        "/v1/notebooklm/status",
        headers={
            "Origin": ORIGIN,
            "Authorization": "Bearer valid-capability extra",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Sessão do aplicativo necessária"


async def test_cors_preflight_allows_only_the_configured_origin(
    client: AsyncTestClient[Litestar],
) -> None:
    headers = {
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }
    allowed = await client.options("/v1/notebooklm/login", headers=headers)
    denied = await client.options(
        "/v1/notebooklm/login",
        headers={**headers, "Origin": "https://attacker.example"},
    )

    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert denied.headers.get("access-control-allow-origin") is None
