from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Final

import httpx
from litestar import Request
from litestar.exceptions import (
    NotAuthorizedException,
    ServiceUnavailableException,
)
from pydantic import BaseModel, ValidationError

from flashcards_generator.delivery.companion.config import CompanionSettings


class CompanionIdentity(BaseModel):
    subject: str


TokenVerifier = Callable[[str, CompanionSettings], Awaitable[str | None]]
_BEARER_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"Bearer (\S+)", re.IGNORECASE | re.ASCII
)
_USER_ID = re.compile(r"[a-f0-9]{32}\Z")


async def verify_remote_token(
    token: str, settings: CompanionSettings
) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{settings.web_origin}/api/v1/auth/companion/verify",
                json={"access_token": token},
            )
    except httpx.HTTPError as error:
        raise ServiceUnavailableException(
            detail="Não foi possível validar a sessão do aplicativo."
        ) from error
    if response.status_code == 401:
        return None
    if response.status_code != 201:
        raise ServiceUnavailableException(
            detail="Não foi possível validar a sessão do aplicativo."
        )
    try:
        return CompanionIdentity.model_validate_json(response.content).subject
    except ValidationError as error:
        raise ServiceUnavailableException(
            detail="A validação da sessão retornou uma resposta inválida."
        ) from error


async def authorized_user(request: Request) -> str:
    settings: CompanionSettings = request.app.state.settings
    if request.headers.get("origin") != settings.web_origin:
        raise NotAuthorizedException(detail="Origem não permitida")
    authorization = request.headers.get("authorization", "")
    credentials = _BEARER_TOKEN.fullmatch(authorization)
    if credentials is None:
        raise NotAuthorizedException(detail="Sessão do aplicativo necessária")
    token = credentials[1]
    verifier: TokenVerifier = request.app.state.token_verifier
    subject = await verifier(token, settings)
    if subject is None or _USER_ID.fullmatch(subject) is None:
        raise NotAuthorizedException(detail="Sessão do aplicativo expirada")
    return subject
