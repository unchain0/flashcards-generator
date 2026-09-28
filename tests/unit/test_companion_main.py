from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from litestar import Litestar

from flashcards_generator.delivery.companion import main as companion_main


def test_companion_main_starts_the_local_litestar_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "FLASHCARDS_COMPANION_WEB_ORIGIN",
        "https://flashcards.example.com",
    )
    monkeypatch.setenv("FLASHCARDS_COMPANION_DATA_DIR", str(tmp_path))
    run = Mock()
    monkeypatch.setattr(companion_main.uvicorn, "run", run)

    companion_main.main()

    run.assert_called_once()
    assert run.call_args is not None
    app = run.call_args.args[0]
    assert isinstance(app, Litestar)
    try:
        assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8765}
        assert (
            app.state.settings.web_origin == "https://flashcards.example.com"
        )
    finally:
        app.state.jobs.close()
