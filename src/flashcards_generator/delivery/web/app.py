from __future__ import annotations

from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.datastructures import MutableScopeHeaders, State
from litestar.middleware import ASGIMiddleware
from litestar.openapi import OpenAPIConfig
from litestar.static_files import create_static_files_router
from litestar.types import ASGIApp, Message, Receive, Scope, Send

from flashcards_generator.delivery.web.config import (
    WebSettings,
    get_settings,
)
from flashcards_generator.delivery.web.routes import (
    auth_router,
    live,
    ready,
)
from flashcards_generator.integrations.web_database import WebDatabase
from flashcards_generator.services.web_authentication import WebAuthentication


class SecurityHeadersMiddleware(ASGIMiddleware):
    async def handle(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        next_app: ASGIApp,
    ) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableScopeHeaders.from_message(message)
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self'; connect-src 'self' "
                    "http://127.0.0.1:8766 https://o4505598204248064.ingest.us.sentry.io; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                )
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                if scope["app"].state.settings.environment == "production":
                    headers["Strict-Transport-Security"] = "max-age=31536000"
                headers["Permissions-Policy"] = (
                    "camera=(), geolocation=(), microphone=()"
                )
            await send(message)

        await next_app(scope, receive, send_with_headers)


async def start_database(app: Litestar) -> None:
    settings: WebSettings = app.state.settings
    database = WebDatabase(
        database_url=settings.database_url,
        session_secret=settings.session_secret.get_secret_value(),
        lookup_secret=settings.auth_lookup_secret.get_secret_value(),
        bootstrap_password=(
            settings.bootstrap_password.get_secret_value()
            if settings.bootstrap_password is not None
            else None
        ),
        auto_create_schema=settings.auto_create_schema,
    )
    app.state.database = database
    app.state.authentication = WebAuthentication(
        database, settings.session_ttl_seconds
    )
    await database.start()


async def stop_database(app: Litestar) -> None:
    database = getattr(app.state, "database", None)
    if database is not None:
        await database.close()


def create_app(settings: WebSettings | None = None) -> Litestar:
    runtime = settings or get_settings()
    built_static_dir = runtime.static_dir / "dist"
    if not built_static_dir.is_dir():
        raise RuntimeError(
            "Frontend compilado ausente; execute `pnpm run build` em frontend/."
        )
    static_router = create_static_files_router(
        path="/",
        directories=[built_static_dir],
        html_mode=True,
        include_in_schema=False,
        name="dashboard",
    )
    return Litestar(
        route_handlers=[live, ready, auth_router, static_router],
        cors_config=CORSConfig(
            allow_origins=runtime.cors_origins,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type"],
        ),
        debug=runtime.environment != "production",
        middleware=[SecurityHeadersMiddleware()],
        on_startup=[start_database],
        on_shutdown=[stop_database],
        openapi_config=OpenAPIConfig(
            title=runtime.app_name,
            version="1.0.0",
        ),
        state=State({"settings": runtime}),
    )


app = create_app()
