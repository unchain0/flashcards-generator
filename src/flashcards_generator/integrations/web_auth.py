from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from base64 import b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from typing import Final

import anyio
from anyio.to_thread import run_sync
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from flashcards_generator.integrations.web_models import (
    WebSessionRecord,
    WebUserRecord,
)
from flashcards_generator.services.web_authentication import (
    COMPANION_TOKEN_TTL_SECONDS,
)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
PASSWORD_HASH_CONCURRENCY: Final = 2
COMPANION_TOKEN_AUDIENCE: Final = "flashcards-companion"


class _CompanionClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str
    expires_at: int
    issued_at: int
    nonce: str
    session_hash: str
    subject: str


class PasswordLengthError(ValueError):
    def __init__(self, minimum: int, maximum: int) -> None:
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(
            f"A senha deve ter entre {minimum} e {maximum} caracteres"
        )


class PasswordAlreadyProvisionedError(ValueError):
    def __init__(self) -> None:
        super().__init__("Essa senha já está provisionada")


class WebAuthService:
    def __init__(self, *, session_secret: str, lookup_secret: str) -> None:
        self._session_secret = session_secret.encode("utf-8")
        self._lookup_secret = lookup_secret.encode("utf-8")
        self._hasher = PasswordHasher()
        self._hash_limiter = anyio.CapacityLimiter(PASSWORD_HASH_CONCURRENCY)

    @staticmethod
    def validate_password(password: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise PasswordLengthError(MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH)

    def _lookup(self, password: str) -> str:
        return hmac.new(
            self._lookup_secret, password.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _token_hash(self, token: str) -> str:
        return hmac.new(
            self._session_secret, token.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def create_user(self, session: AsyncSession, password: str) -> str:
        self.validate_password(password)
        lookup = self._lookup(password)
        existing = await session.scalar(
            select(WebUserRecord).where(
                WebUserRecord.password_lookup == lookup
            )
        )
        if existing is not None:
            raise PasswordAlreadyProvisionedError()
        user_id = secrets.token_hex(16)
        session.add(
            WebUserRecord(
                id=user_id,
                password_lookup=lookup,
                password_hash=await self._hash_password(password),
            )
        )
        await session.commit()
        return user_id

    async def ensure_bootstrap(
        self, session: AsyncSession, password: str | None
    ) -> None:
        if password is None:
            return
        self.validate_password(password)
        lookup = self._lookup(password)
        existing = await session.scalar(
            select(WebUserRecord.id).where(
                WebUserRecord.password_lookup == lookup
            )
        )
        if existing is None:
            session.add(
                WebUserRecord(
                    id=secrets.token_hex(16),
                    password_lookup=lookup,
                    password_hash=await self._hash_password(password),
                )
            )
            await session.commit()

    async def authenticate(
        self, session: AsyncSession, password: str
    ) -> str | None:
        if not password or len(password) > MAX_PASSWORD_LENGTH:
            return None
        lookup = self._lookup(password)
        record = await session.scalar(
            select(WebUserRecord).where(
                WebUserRecord.password_lookup == lookup
            )
        )
        if record is None or not await self._verify_password(
            record.password_hash, password
        ):
            return None
        await self._upgrade_password_hash(session, record, password)
        return record.id

    async def _upgrade_password_hash(
        self, session: AsyncSession, record: WebUserRecord, password: str
    ) -> None:
        if self._hasher.check_needs_rehash(record.password_hash):
            record.password_hash = await self._hash_password(password)
            await session.commit()

    async def _hash_password(self, password: str) -> str:
        return await run_sync(
            self._hasher.hash,
            password,
            limiter=self._hash_limiter,
        )

    async def _verify_password(
        self, password_hash: str, password: str
    ) -> bool:
        try:
            return await run_sync(
                self._hasher.verify,
                password_hash,
                password,
                limiter=self._hash_limiter,
            )
        except InvalidHashError, VerificationError:
            return False

    async def create_session(
        self,
        session: AsyncSession,
        user_id: str,
        ttl_seconds: int,
    ) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            WebSessionRecord(
                token_hash=self._token_hash(token),
                user_id=user_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            )
        )
        await session.execute(
            delete(WebSessionRecord).where(
                WebSessionRecord.expires_at <= datetime.now(UTC)
            )
        )
        await session.commit()
        return token

    async def user_for_session(
        self, session: AsyncSession, token: str | None
    ) -> str | None:
        if not token:
            return None
        record = await session.scalar(
            select(WebSessionRecord).where(
                WebSessionRecord.token_hash == self._token_hash(token)
            )
        )
        if record is None:
            return None
        now = datetime.now(UTC)
        if _session_expired(record, now):
            await session.delete(record)
            await session.commit()
            return None
        return record.user_id

    async def delete_session(
        self, session: AsyncSession, token: str | None
    ) -> None:
        if token:
            await session.execute(
                delete(WebSessionRecord).where(
                    WebSessionRecord.token_hash == self._token_hash(token)
                )
            )
            await session.commit()

    async def create_companion_token(
        self, session: AsyncSession, token: str | None
    ) -> str | None:
        user_id = await self.user_for_session(session, token)
        if user_id is None or token is None:
            return None
        issued_at = int(time.time())
        claims = _CompanionClaims(
            audience=COMPANION_TOKEN_AUDIENCE,
            expires_at=issued_at + COMPANION_TOKEN_TTL_SECONDS,
            issued_at=issued_at,
            nonce=secrets.token_urlsafe(16),
            session_hash=self._token_hash(token),
            subject=user_id,
        )
        payload = urlsafe_b64encode(
            claims.model_dump_json().encode("utf-8")
        ).rstrip(b"=")
        signature = hmac.new(
            self._session_secret,
            b"flashcards-companion\0" + payload,
            hashlib.sha256,
        ).digest()
        encoded_signature = urlsafe_b64encode(signature).rstrip(b"=")
        return f"{payload.decode('ascii')}.{encoded_signature.decode('ascii')}"

    async def user_for_companion_token(
        self, session: AsyncSession, token: str
    ) -> str | None:
        claims = _verified_companion_claims(
            token, self._session_secret, int(time.time())
        )
        if claims is None:
            return None

        record = await session.get(WebSessionRecord, claims.session_hash)
        if record is None or record.user_id != claims.subject:
            return None
        if _session_expired(record, datetime.now(UTC)):
            return None
        return record.user_id


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return b64decode(value + padding, altchars=b"-_", validate=True)


def _verified_companion_payload(token: str, secret: bytes) -> str | None:
    if len(token) > 4096:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".")
        payload = encoded_payload.encode("ascii")
        signature = _decode_base64url(encoded_signature)
    except UnicodeEncodeError, ValueError:
        return None

    expected_signature = hmac.new(
        secret,
        b"flashcards-companion\0" + payload,
        hashlib.sha256,
    ).digest()
    return (
        encoded_payload
        if hmac.compare_digest(signature, expected_signature)
        else None
    )


def _current_companion_claims(
    encoded_payload: str, now: int
) -> _CompanionClaims | None:
    try:
        claims = _CompanionClaims.model_validate_json(
            _decode_base64url(encoded_payload)
        )
    except ValueError, ValidationError:
        return None
    return claims if _claims_are_current(claims, now) else None


def _verified_companion_claims(
    token: str, secret: bytes, now: int
) -> _CompanionClaims | None:
    encoded_payload = _verified_companion_payload(token, secret)
    if encoded_payload is None:
        return None
    return _current_companion_claims(encoded_payload, now)


def _claims_are_current(claims: _CompanionClaims, now: int) -> bool:
    return all((
        claims.audience == COMPANION_TOKEN_AUDIENCE,
        claims.issued_at <= now,
        now < claims.expires_at,
        claims.expires_at <= claims.issued_at + COMPANION_TOKEN_TTL_SECONDS,
        len(claims.session_hash) == 64,
        len(claims.subject) == 32,
    ))


def _session_expired(record: WebSessionRecord, now: datetime) -> bool:
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now
