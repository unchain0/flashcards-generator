from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from flashcards_generator.integrations import web_auth
from flashcards_generator.integrations.web_auth import (
    PasswordAlreadyProvisionedError,
    PasswordLengthError,
    WebAuthService,
)
from flashcards_generator.integrations.web_models import (
    WebBase,
    WebSessionRecord,
    WebUserRecord,
)

pytestmark = pytest.mark.anyio

PASSWORD: Final = "test-password-123"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def auth_service() -> WebAuthService:
    return WebAuthService(
        session_secret="session-secret-for-test",
        lookup_secret="lookup-secret-for-test",
    )


@pytest.fixture
async def sessions(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(WebBase.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def stored_user(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
) -> str:
    async with sessions() as session:
        return await auth_service.create_user(session, PASSWORD)


@pytest.mark.parametrize("password", ("x" * 12, "x" * 256))
def test_password_length_limits_are_inclusive(password: str) -> None:
    WebAuthService.validate_password(password)


@pytest.mark.parametrize("password", ("x" * 11, "x" * 257))
def test_password_length_limits_reject_out_of_range_values(
    password: str,
) -> None:
    with pytest.raises(PasswordLengthError):
        WebAuthService.validate_password(password)


async def test_create_user_stores_password_lookup_and_argon2_hash(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
) -> None:
    async with sessions() as session:
        user_id = await auth_service.create_user(session, PASSWORD)
        record = await session.get(WebUserRecord, user_id)

    assert record is not None
    assert record.password_lookup == auth_service._lookup(PASSWORD)
    assert auth_service._hasher.verify(record.password_hash, PASSWORD)


async def test_create_user_rejects_an_existing_password(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    async with sessions() as session:
        with pytest.raises(PasswordAlreadyProvisionedError):
            await auth_service.create_user(session, PASSWORD)


async def test_bootstrap_without_a_password_does_not_create_a_user(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
) -> None:
    async with sessions() as session:
        await auth_service.ensure_bootstrap(session, None)
        record = await session.scalar(select(WebUserRecord))

    assert record is None


async def test_bootstrap_creates_only_one_user_for_a_password(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
) -> None:
    async with sessions() as session:
        await auth_service.ensure_bootstrap(session, PASSWORD)
        await auth_service.ensure_bootstrap(session, PASSWORD)
        records = (await session.scalars(select(WebUserRecord))).all()

    assert len(records) == 1
    assert auth_service._hasher.verify(records[0].password_hash, PASSWORD)


@pytest.mark.parametrize("password", ("", "x" * 257))
async def test_authenticate_rejects_blank_or_overlong_passwords(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    password: str,
) -> None:
    async with sessions() as session:
        user_id = await auth_service.authenticate(session, password)

    assert user_id is None


async def test_authenticate_returns_none_when_no_user_matches(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
) -> None:
    async with sessions() as session:
        user_id = await auth_service.authenticate(session, PASSWORD)

    assert user_id is None


async def test_authenticate_rejects_a_malformed_password_hash(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
) -> None:
    async with sessions() as session:
        session.add(
            WebUserRecord(
                id="malformed",
                password_lookup=auth_service._lookup(PASSWORD),
                password_hash="not-an-argon2-hash",
            )
        )
        await session.commit()
        user_id = await auth_service.authenticate(session, PASSWORD)

    assert user_id is None


async def test_authenticate_upgrades_an_outdated_password_hash(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PasswordHasher,
        "check_needs_rehash",
        lambda _hasher, _encoded: True,
    )

    async with sessions() as session:
        authenticated_user = await auth_service.authenticate(session, PASSWORD)
        record = await session.get(WebUserRecord, stored_user)

    assert authenticated_user == stored_user
    assert record is not None
    assert auth_service._hasher.verify(record.password_hash, PASSWORD)


@pytest.mark.parametrize("token", (None, "unknown-token"))
async def test_user_for_session_rejects_missing_or_unknown_tokens(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    token: str | None,
) -> None:
    async with sessions() as session:
        user_id = await auth_service.user_for_session(session, token)

    assert user_id is None


async def test_user_for_session_accepts_a_valid_token(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    async with sessions() as session:
        token = await auth_service.create_session(session, stored_user, 3600)
        user_id = await auth_service.user_for_session(session, token)

    assert user_id == stored_user


async def test_user_for_session_removes_expired_tokens(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    token = "expired-token"
    async with sessions() as session:
        session.add(
            WebSessionRecord(
                token_hash=auth_service._token_hash(token),
                user_id=stored_user,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()
        user_id = await auth_service.user_for_session(session, token)
        record = await session.get(
            WebSessionRecord, auth_service._token_hash(token)
        )

    assert user_id is None
    assert record is None


async def test_create_session_hashes_tokens_and_cleans_expired_sessions(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    expired_token = "old-token"
    async with sessions() as session:
        session.add(
            WebSessionRecord(
                token_hash=auth_service._token_hash(expired_token),
                user_id=stored_user,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()
        token = await auth_service.create_session(session, stored_user, 3600)
        saved = await session.get(
            WebSessionRecord, auth_service._token_hash(token)
        )
        expired = await session.get(
            WebSessionRecord, auth_service._token_hash(expired_token)
        )

    assert saved is not None
    assert saved.token_hash != token
    assert expired is None


async def test_delete_session_without_a_token_leaves_the_session_active(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    async with sessions() as session:
        token = await auth_service.create_session(session, stored_user, 3600)
        await auth_service.delete_session(session, None)
        user_id = await auth_service.user_for_session(session, token)

    assert user_id == stored_user


async def test_delete_session_revokes_the_token(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    async with sessions() as session:
        token = await auth_service.create_session(session, stored_user, 3600)
        await auth_service.delete_session(session, token)
        user_id = await auth_service.user_for_session(session, token)

    assert user_id is None


async def test_companion_token_is_bound_to_an_active_web_session(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    async with sessions() as session:
        web_token = await auth_service.create_session(
            session, stored_user, 3600
        )
        companion_token = await auth_service.create_companion_token(
            session, web_token
        )
        assert companion_token is not None
        assert web_token not in companion_token
        assert (
            await auth_service.user_for_companion_token(
                session, companion_token
            )
            == stored_user
        )
        await auth_service.delete_session(session, web_token)
        assert (
            await auth_service.user_for_companion_token(
                session, companion_token
            )
            is None
        )


async def test_companion_token_rejects_invalid_expired_and_missing_sessions(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with sessions() as session:
        web_token = await auth_service.create_session(
            session, stored_user, 3600
        )
        companion_token = await auth_service.create_companion_token(
            session, web_token
        )
        assert companion_token is not None
        payload, signature = companion_token.split(".")
        tampered = (
            f"{'A' if payload[0] != 'A' else 'B'}{payload[1:]}.{signature}"
        )

        assert (
            await auth_service.user_for_companion_token(session, "bad") is None
        )
        assert (
            await auth_service.user_for_companion_token(session, tampered)
            is None
        )
        assert (
            await auth_service.user_for_companion_token(session, "x" * 4097)
            is None
        )

        monkeypatch.setattr(web_auth.time, "time", lambda: 10**12)
        assert (
            await auth_service.user_for_companion_token(
                session, companion_token
            )
            is None
        )


async def test_companion_token_rejects_an_expired_web_session(
    sessions: async_sessionmaker[AsyncSession],
    auth_service: WebAuthService,
    stored_user: str,
) -> None:
    async with sessions() as session:
        web_token = await auth_service.create_session(
            session, stored_user, 3600
        )
        companion_token = await auth_service.create_companion_token(
            session, web_token
        )
        assert companion_token is not None
        record = await session.get(
            WebSessionRecord, auth_service._token_hash(web_token)
        )
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        assert (
            await auth_service.user_for_companion_token(
                session, companion_token
            )
            is None
        )
