from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from litestar.exceptions import ServiceUnavailableException

from flashcards_generator.delivery.companion.auth import verify_remote_token
from flashcards_generator.delivery.companion.config import CompanionSettings

pytestmark = pytest.mark.anyio

ORIGIN = "https://flashcards.example.com"
USER_ID = "a" * 32


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> CompanionSettings:
    return CompanionSettings(web_origin=ORIGIN, data_dir=tmp_path)


@pytest.fixture
def install_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    original_client = httpx.AsyncClient

    def install(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        transport = httpx.MockTransport(handler)

        def client_factory(
            *, timeout: float | httpx.Timeout | None = None
        ) -> httpx.AsyncClient:
            return original_client(transport=transport, timeout=timeout)

        monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    return install


async def test_remote_token_returns_identity_from_created_response(
    settings: CompanionSettings,
    install_transport: Callable[
        [Callable[[httpx.Request], httpx.Response]], None
    ],
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == (f"{ORIGIN}/api/v1/auth/companion/verify")
        assert request.read() == b'{"access_token":"capability"}'
        return httpx.Response(201, json={"subject": USER_ID})

    install_transport(respond)

    subject = await verify_remote_token("capability", settings)

    assert subject == USER_ID


async def test_remote_token_returns_none_for_unauthorized_response(
    settings: CompanionSettings,
    install_transport: Callable[
        [Callable[[httpx.Request], httpx.Response]], None
    ],
) -> None:
    install_transport(lambda _request: httpx.Response(401))

    subject = await verify_remote_token("revoked", settings)

    assert subject is None


async def test_remote_token_rejects_unexpected_status(
    settings: CompanionSettings,
    install_transport: Callable[
        [Callable[[httpx.Request], httpx.Response]], None
    ],
) -> None:
    install_transport(lambda _request: httpx.Response(503))

    with pytest.raises(ServiceUnavailableException) as failure:
        await verify_remote_token("capability", settings)

    assert failure.value.status_code == 503


async def test_remote_token_rejects_invalid_identity_response(
    settings: CompanionSettings,
    install_transport: Callable[
        [Callable[[httpx.Request], httpx.Response]], None
    ],
) -> None:
    install_transport(lambda _request: httpx.Response(201, json={}))

    with pytest.raises(ServiceUnavailableException) as failure:
        await verify_remote_token("capability", settings)

    assert failure.value.status_code == 503
    assert failure.value.detail == (
        "A validação da sessão retornou uma resposta inválida."
    )


async def test_remote_token_translates_transport_failure(
    settings: CompanionSettings,
    install_transport: Callable[
        [Callable[[httpx.Request], httpx.Response]], None
    ],
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    install_transport(fail)

    with pytest.raises(ServiceUnavailableException) as failure:
        await verify_remote_token("capability", settings)

    assert failure.value.status_code == 503
    assert failure.value.detail == (
        "Não foi possível validar a sessão do aplicativo."
    )
