from __future__ import annotations

import argparse
from getpass import getpass

import anyio
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flashcards_generator.delivery.web.config import get_settings
from flashcards_generator.integrations.web_auth import WebAuthService
from flashcards_generator.integrations.web_models import (
    WebSessionRecord,
    WebUserRecord,
)


async def create_user(password: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    auth = WebAuthService(
        session_secret=settings.session_secret.get_secret_value(),
        lookup_secret=settings.auth_lookup_secret.get_secret_value(),
    )
    try:
        async with engine.connect() as connection:
            has_auth_schema = await connection.run_sync(
                lambda sync_connection: all(
                    inspect(sync_connection).has_table(table)
                    for table in (
                        WebUserRecord.__tablename__,
                        WebSessionRecord.__tablename__,
                    )
                )
            )
        if not has_auth_schema:
            raise RuntimeError(
                "Execute as migrações do banco antes de criar usuários"
            )
        async with sessions() as session:
            await auth.create_user(session, password)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provisiona acessos ao Gerador de flashcards"
    )
    parser.add_argument("command", choices=("create",))
    parser.parse_args()
    password = getpass("Senha de acesso: ")
    confirmation = getpass("Confirme a senha: ")
    if password != confirmation:
        parser.error("As senhas não conferem")
    try:
        anyio.run(create_user, password)
    except ValueError as error:
        parser.error(str(error))
    print("Acesso criado.")


if __name__ == "__main__":
    main()
