from __future__ import annotations

from litestar import Request, Router, get, post
from litestar.datastructures import Cookie, State
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    SerializationException,
)
from litestar.response import Response

from flashcards_generator.delivery.web.auth import (
    SESSION_COOKIE_NAME,
    require_auth,
    session_token,
)
from flashcards_generator.delivery.web.schemas import (
    AuthRead,
    CompanionIdentityRead,
    CompanionTokenRead,
    CompanionTokenVerify,
)
from flashcards_generator.services.web_authentication import (
    COMPANION_TOKEN_TTL_SECONDS,
    WebAuthentication,
)


@post("/login", status_code=200)
async def login(request: Request, state: State) -> Response[dict[str, bool]]:
    try:
        payload = await request.json()
    except (SerializationException, ValueError) as error:
        raise HTTPException(
            detail="Informe a senha", status_code=400
        ) from error
    password = payload.get("password") if isinstance(payload, dict) else None
    if not isinstance(password, str):
        raise HTTPException(detail="Informe a senha", status_code=400)
    authentication: WebAuthentication = state.authentication
    token = await authentication.login(password)
    if token is None:
        raise NotAuthorizedException(detail="Senha inválida")
    return Response(
        content={"authenticated": True},
        cookies=[
            Cookie(
                key=SESSION_COOKIE_NAME,
                value=token,
                max_age=state.settings.session_ttl_seconds,
                secure=state.settings.environment == "production",
                httponly=True,
                samesite="lax",
            )
        ],
    )


@post("/logout", status_code=200)
async def logout(request: Request, state: State) -> Response[dict[str, bool]]:
    authentication: WebAuthentication = state.authentication
    await authentication.logout(session_token(request))
    return Response(
        content={"authenticated": False},
        cookies=[
            Cookie(
                key=SESSION_COOKIE_NAME,
                value="",
                max_age=0,
                secure=state.settings.environment == "production",
                httponly=True,
                samesite="lax",
            )
        ],
    )


@get("/me", guards=[require_auth])
async def me(request: Request) -> AuthRead:
    del request
    return AuthRead(authenticated=True)


@post("/companion/token", guards=[require_auth])
async def companion_token(
    request: Request, state: State
) -> CompanionTokenRead:
    authentication: WebAuthentication = state.authentication
    token = await authentication.create_companion_token(session_token(request))
    if token is None:
        raise NotAuthorizedException(detail="Authentication required")
    return CompanionTokenRead(
        access_token=token,
        expires_in=COMPANION_TOKEN_TTL_SECONDS,
    )


@post("/companion/verify")
async def verify_companion_token(
    data: CompanionTokenVerify, state: State
) -> CompanionIdentityRead:
    authentication: WebAuthentication = state.authentication
    user_id = await authentication.user_for_companion_token(data.access_token)
    if user_id is None:
        raise NotAuthorizedException(detail="Invalid companion token")
    return CompanionIdentityRead(subject=user_id)


auth_router = Router(
    path="/api/v1/auth",
    route_handlers=[
        login,
        logout,
        me,
        companion_token,
        verify_companion_token,
    ],
    tags=["authentication"],
)
