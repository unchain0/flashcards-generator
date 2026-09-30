from __future__ import annotations

import os
from pathlib import Path

import pytest
from litestar import Litestar

from flashcards_generator.delivery.web.app import create_app, stop_database
from flashcards_generator.delivery.web.config import WebSettings


@pytest.fixture(autouse=True)
def isolate_flashcards_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("FLASHCARDS_"):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("dist_is_file", [False, True])
def test_create_app_rejects_missing_frontend_distribution(
    tmp_path: Path, dist_is_file: bool
) -> None:
    dist = tmp_path / "dist"
    if dist_is_file:
        dist.write_text("not a directory", encoding="utf-8")

    settings = WebSettings(
        environment="test",
        static_dir=tmp_path,
        _env_file=None,
    )

    with pytest.raises(
        RuntimeError,
        match=r"Frontend compilado ausente.*pnpm run build",
    ):
        create_app(settings)


def test_create_app_accepts_a_frontend_distribution(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<main>synthetic frontend</main>", encoding="utf-8"
    )
    settings = WebSettings(
        environment="test",
        static_dir=tmp_path,
        _env_file=None,
    )

    app = create_app(settings)

    assert isinstance(app, Litestar)
    assert app.state.settings is settings


@pytest.mark.asyncio
async def test_database_shutdown_before_startup_is_safe() -> None:
    app = Litestar()

    await stop_database(app)

    assert not hasattr(app.state, "database")
