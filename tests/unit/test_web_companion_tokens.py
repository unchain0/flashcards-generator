from __future__ import annotations

import hashlib
import hmac
from base64 import urlsafe_b64encode
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flashcards_generator.integrations.web_auth import WebAuthService

pytestmark = pytest.mark.anyio

SESSION_SECRET = b"session-secret-for-companion-tests"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_companion_token_creation_requires_a_web_session() -> None:
    auth = WebAuthService(
        session_secret=SESSION_SECRET.decode("ascii"),
        lookup_secret="lookup-secret-for-companion-tests",
    )
    session = AsyncMock(spec=AsyncSession)

    assert await auth.create_companion_token(session, None) is None
    session.scalar.assert_not_awaited()


async def test_companion_token_rejects_a_signed_invalid_claim_payload() -> (
    None
):
    auth = WebAuthService(
        session_secret=SESSION_SECRET.decode("ascii"),
        lookup_secret="lookup-secret-for-companion-tests",
    )
    payload = urlsafe_b64encode(b"{").rstrip(b"=")
    signature = hmac.new(
        SESSION_SECRET,
        b"flashcards-companion\0" + payload,
        hashlib.sha256,
    ).digest()
    token = ".".join((
        payload.decode("ascii"),
        urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
    ))
    session = AsyncMock(spec=AsyncSession)

    assert await auth.user_for_companion_token(session, token) is None
    session.get.assert_not_awaited()
