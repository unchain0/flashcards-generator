from __future__ import annotations

from threading import get_ident
from typing import Final

import pytest
from argon2 import PasswordHasher

from flashcards_generator.integrations.web_auth import WebAuthService

pytestmark = pytest.mark.anyio

PASSWORD: Final = "a sufficiently long password"


@pytest.fixture
def auth_service() -> WebAuthService:
    return WebAuthService(
        session_secret="session-secret-for-test",
        lookup_secret="lookup-secret-for-test",
    )


async def test_password_hashing_runs_in_a_bounded_worker_pool(
    auth_service: WebAuthService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_threads: list[int] = []

    def hash_password(_hasher: PasswordHasher, password: str) -> str:
        assert password == PASSWORD
        worker_threads.append(get_ident())
        return "argon2-test-hash"

    monkeypatch.setattr(PasswordHasher, "hash", hash_password)

    encoded = await auth_service._hash_password(PASSWORD)

    assert encoded == "argon2-test-hash"
    assert len(worker_threads) == 1
    assert worker_threads[0] != get_ident()
    assert auth_service._hash_limiter.total_tokens == 2


async def test_password_verification_runs_in_a_bounded_worker_pool(
    auth_service: WebAuthService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_threads: list[int] = []

    def verify_password(
        _hasher: PasswordHasher, encoded: str, password: str
    ) -> bool:
        assert encoded == "argon2-test-hash"
        assert password == PASSWORD
        worker_threads.append(get_ident())
        return True

    monkeypatch.setattr(PasswordHasher, "verify", verify_password)

    verified = await auth_service._verify_password(
        "argon2-test-hash", PASSWORD
    )

    assert verified is True
    assert len(worker_threads) == 1
    assert worker_threads[0] != get_ident()
    assert auth_service._hash_limiter.total_tokens == 2
