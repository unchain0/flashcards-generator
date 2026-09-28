from __future__ import annotations

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import BaseRouteHandler

from flashcards_generator.services.web_authentication import WebAuthentication

SESSION_COOKIE_NAME = "flashcards_session"


def session_token(connection: ASGIConnection) -> str | None:
    return connection.cookies.get(SESSION_COOKIE_NAME)


async def require_auth(
    connection: ASGIConnection, handler: BaseRouteHandler
) -> None:
    del handler
    authentication: WebAuthentication = connection.app.state.authentication
    user_id = await authentication.user_for_session(session_token(connection))
    if user_id is None:
        raise NotAuthorizedException(
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    connection.scope["state"]["auth_user_id"] = user_id


def authenticated_user_id(connection: ASGIConnection) -> str:
    user_id: object = connection.scope["state"].get("auth_user_id")
    if not isinstance(user_id, str):
        raise NotAuthorizedException(detail="Authentication required")
    return user_id
