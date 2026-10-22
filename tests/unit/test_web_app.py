from __future__ import annotations

import os
from pathlib import Path

import pytest
from litestar import Litestar, get
from litestar.datastructures import State
from litestar.testing import TestClient

from flashcards_generator.delivery.web.app import (
    SecurityHeadersMiddleware,
    create_app,
    stop_database,
)
from flashcards_generator.delivery.web.config import Environment, WebSettings


@pytest.fixture(autouse=True)
def isolate_flashcards_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("FLASHCARDS_"):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("dist_is_file", [False, True])
def test_create_app_rejects_missing_frontend_distribution(
    tmp_path: Path, dist_is_file: bool
) -> None:
    dist = tmp_path / "dist"
    if dist_is_file:
        dist.write_text("not a directory", encoding="utf-8")

    settings = WebSettings(
        environment="test",
        static_dir=tmp_path,
        _env_file=None,
    )

    with pytest.raises(
        RuntimeError,
        match=r"Frontend compilado ausente.*pnpm run build",
    ):
        create_app(settings)


def test_create_app_accepts_a_frontend_distribution(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<main>synthetic frontend</main>", encoding="utf-8"
    )
    settings = WebSettings(
        environment="test",
        static_dir=tmp_path,
        _env_file=None,
    )

    app = create_app(settings)

    assert isinstance(app, Litestar)
    assert app.state.settings is settings


@pytest.mark.parametrize("environment", ["production", "test"])
def test_transport_security_header_is_production_only(
    environment: Environment,
) -> None:
    settings = WebSettings(
        environment=environment,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        session_secret="session-secret-test-0123456789abcdef",
        auth_lookup_secret="lookup-secret-test-0123456789abcdef",
        auto_create_schema=False,
        _env_file=None,
    )

    @get("/")
    def page() -> dict[str, str]:
        return {"status": "ok"}

    app = Litestar(
        route_handlers=[page],
        middleware=[SecurityHeadersMiddleware()],
        state=State({"settings": settings}),
    )
    with TestClient(app) as client:
        response = client.get("/")

    expected = "max-age=31536000" if environment == "production" else None
    assert response.headers.get("strict-transport-security") == expected


def test_security_headers_are_present_in_development() -> None:
    settings = WebSettings(
        environment="test",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        session_secret="session-secret-test-0123456789abcdef",
        auth_lookup_secret="lookup-secret-test-0123456789abcdef",
        auto_create_schema=False,
        _env_file=None,
    )

    @get("/")
    def page() -> dict[str, str]:
        return {"status": "ok"}

    app = Litestar(
        route_handlers=[page],
        middleware=[SecurityHeadersMiddleware()],
        state=State({"settings": settings}),
    )
    with TestClient(app) as client:
        response = client.get("/")

    # Content-Security-Policy
    csp = response.headers.get("content-security-policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "img-src 'self'" in csp
    assert "connect-src 'self' http://127.0.0.1:8766" in csp
    assert csp == (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self' http://127.0.0.1:8766 "
        "https://o4505598204248064.ingest.us.sentry.io; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp

    # Referrer-Policy
    assert response.headers.get("referrer-policy") == "no-referrer"

    # X-Content-Type-Options
    assert response.headers.get("x-content-type-options") == "nosniff"

    # X-Frame-Options
    assert response.headers.get("x-frame-options") == "DENY"

    # Permissions-Policy
    permissions = response.headers.get("permissions-policy")
    assert permissions is not None
    assert "camera=()" in permissions
    assert "geolocation=()" in permissions
    assert "microphone=()" in permissions

    # Cache-Control
    assert response.headers.get("cache-control") == "no-store"

    # HSTS should NOT be present in development
    assert response.headers.get("strict-transport-security") is None


def test_security_headers_include_hsts_in_production() -> None:
    settings = WebSettings(
        environment="production",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        session_secret="session-secret-test-0123456789abcdef",
        auth_lookup_secret="lookup-secret-test-0123456789abcdef",
        auto_create_schema=False,
        _env_file=None,
    )

    @get("/")
    def page() -> dict[str, str]:
        return {"status": "ok"}

    app = Litestar(
        route_handlers=[page],
        middleware=[SecurityHeadersMiddleware()],
        state=State({"settings": settings}),
    )
    with TestClient(app) as client:
        response = client.get("/")

    # HSTS should be present in production
    assert (
        response.headers.get("strict-transport-security") == "max-age=31536000"
    )

    # Other headers should also be present
    assert response.headers.get("content-security-policy") is not None
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("permissions-policy") is not None
    assert response.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_database_shutdown_before_startup_is_safe() -> None:
    app = Litestar()

    await stop_database(app)

    assert not hasattr(app.state, "database")
