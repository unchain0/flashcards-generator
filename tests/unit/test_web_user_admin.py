from __future__ import annotations

import getpass
import sys
from pathlib import Path
from runpy import run_path
from typing import Final

import anyio
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flashcards_generator.delivery.web import user_admin
from flashcards_generator.delivery.web.config import WebSettings
from flashcards_generator.integrations.web_models import (
    WebBase,
    WebUserRecord,
)

PASSWORD: Final = "test-password-123"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'users.db'}",
        session_secret=SecretStr("s" * 32),
        auth_lookup_secret=SecretStr("l" * 32),
        auto_create_schema=True,
    )


@pytest.mark.anyio
async def test_create_user_requires_both_auth_tables(
    settings: WebSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_admin, "get_settings", lambda: settings)
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(WebUserRecord.__table__.create)
    await engine.dispose()

    with pytest.raises(RuntimeError):
        await user_admin.create_user(PASSWORD)


@pytest.mark.anyio
async def test_create_user_persists_the_provisioned_hash(
    settings: WebSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_admin, "get_settings", lambda: settings)
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(WebBase.metadata.create_all)
    await engine.dispose()

    await user_admin.create_user(PASSWORD)

    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        record = await session.scalar(select(WebUserRecord))
    await engine.dispose()

    assert record is not None
    assert record.password_hash != PASSWORD


def test_main_rejects_password_confirmation_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter((PASSWORD, "different-password"))
    monkeypatch.setattr(sys, "argv", ["flashcards-user", "create"])
    monkeypatch.setattr(user_admin, "getpass", lambda _: next(answers))

    with pytest.raises(SystemExit) as error:
        user_admin.main()

    assert error.value.code == 2


def test_main_provisions_access_after_matching_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    async def capture_password(password: str) -> None:
        calls.append(password)

    answers = iter((PASSWORD, PASSWORD))
    monkeypatch.setattr(sys, "argv", ["flashcards-user", "create"])
    monkeypatch.setattr(user_admin, "getpass", lambda _: next(answers))
    monkeypatch.setattr(user_admin, "create_user", capture_password)

    user_admin.main()

    assert calls == [PASSWORD]
    assert capsys.readouterr().out.strip() == "Acesso criado."


def test_main_reports_password_provisioning_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_password(_: str) -> None:
        raise ValueError("invalid password")

    answers = iter((PASSWORD, PASSWORD))
    monkeypatch.setattr(sys, "argv", ["flashcards-user", "create"])
    monkeypatch.setattr(user_admin, "getpass", lambda _: next(answers))
    monkeypatch.setattr(user_admin, "create_user", reject_password)

    with pytest.raises(SystemExit) as error:
        user_admin.main()

    assert error.value.code == 2


def test_module_entrypoint_runs_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["flashcards-user", "create"])
    monkeypatch.setattr(getpass, "getpass", lambda _: PASSWORD)
    monkeypatch.setattr(anyio, "run", lambda *_: None)

    run_path(str(Path(user_admin.__file__)), run_name="__main__")
