from __future__ import annotations

from typing import Final, Protocol

COMPANION_TOKEN_TTL_SECONDS: Final = 300


class WebAuthStore(Protocol):
    async def authenticate_and_create_session(
        self, password: str, ttl_seconds: int
    ) -> str | None: ...

    async def delete_session(self, token: str | None) -> None: ...

    async def user_for_session(self, token: str | None) -> str | None: ...

    async def create_companion_token(
        self, token: str | None
    ) -> str | None: ...

    async def user_for_companion_token(self, token: str) -> str | None: ...


class WebAuthentication:
    def __init__(self, store: WebAuthStore, session_ttl_seconds: int) -> None:
        self._store = store
        self._session_ttl_seconds = session_ttl_seconds

    async def login(self, password: str) -> str | None:
        return await self._store.authenticate_and_create_session(
            password, self._session_ttl_seconds
        )

    async def logout(self, token: str | None) -> None:
        await self._store.delete_session(token)

    async def user_for_session(self, token: str | None) -> str | None:
        return await self._store.user_for_session(token)

    async def create_companion_token(self, token: str | None) -> str | None:
        return await self._store.create_companion_token(token)

    async def user_for_companion_token(self, token: str) -> str | None:
        return await self._store.user_for_companion_token(token)
