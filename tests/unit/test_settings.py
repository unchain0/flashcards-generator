"""Tests for settings persistence."""

import json
from pathlib import Path

from flashcards_generator.infrastructure.settings import (
    Settings,
    SettingsRepository,
)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    settings = Settings(
        input_dir=Path("documents"),
        output_dir=Path("decks"),
        language="en_US",
        instructions="Use concise definitions",
        timeout=120,
    )

    repository.save(settings)

    assert repository.load() == settings
    assert (
        json.loads(repository.path.read_text(encoding="utf-8"))["language"]
        == "en_US"
    )


def test_default_path_uses_xdg_config_home(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    repository = SettingsRepository()

    assert (
        repository.path == tmp_path / "flashcards-generator" / "settings.json"
    )


def test_malformed_or_invalid_payload_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    defaults = Settings()

    path.write_text("{not-json", encoding="utf-8")
    assert repository.load() == defaults

    path.write_text(json.dumps({"timeout": 0}), encoding="utf-8")
    assert repository.load() == defaults
