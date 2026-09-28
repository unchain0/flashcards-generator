from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from flashcards_generator.integrations.web_auth import WebAuthService
from flashcards_generator.integrations.web_models import WebBase


class WebDatabase:
    def __init__(
        self,
        *,
        database_url: str,
        session_secret: str,
        lookup_secret: str,
        bootstrap_password: str | None,
        auto_create_schema: bool,
    ) -> None:
        self._bootstrap_password = bootstrap_password
        engine_options: dict[str, int | bool] = {"pool_pre_ping": True}
        if database_url.startswith("postgresql"):
            engine_options.update({"pool_size": 5, "max_overflow": 10})
        self.engine: AsyncEngine = create_async_engine(
            database_url, **engine_options
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self._auto_create_schema = auto_create_schema
        self.auth = WebAuthService(
            session_secret=session_secret,
            lookup_secret=lookup_secret,
        )

    async def start(self) -> None:
        if self._auto_create_schema:
            async with self.engine.begin() as connection:
                await connection.run_sync(WebBase.metadata.create_all)
        try:
            async with self.sessions() as session:
                await self.auth.ensure_bootstrap(
                    session, self._bootstrap_password
                )
        except DBAPIError:
            if self._auto_create_schema:
                raise

    async def check(self) -> None:
        async with self.sessions() as session:
            for table in WebBase.metadata.sorted_tables:
                await session.execute(select(table).limit(0))

    async def authenticate_and_create_session(
        self, password: str, ttl_seconds: int
    ) -> str | None:
        async with self.sessions() as session:
            user_id = await self.auth.authenticate(session, password)
            if user_id is None:
                return None
            return await self.auth.create_session(
                session, user_id, ttl_seconds
            )

    async def delete_session(self, token: str | None) -> None:
        async with self.sessions() as session:
            await self.auth.delete_session(session, token)

    async def user_for_session(self, token: str | None) -> str | None:
        async with self.sessions() as session:
            return await self.auth.user_for_session(session, token)

    async def create_companion_token(self, token: str | None) -> str | None:
        async with self.sessions() as session:
            return await self.auth.create_companion_token(session, token)

    async def user_for_companion_token(self, token: str) -> str | None:
        async with self.sessions() as session:
            return await self.auth.user_for_companion_token(session, token)

    async def close(self) -> None:
        await self.engine.dispose()
