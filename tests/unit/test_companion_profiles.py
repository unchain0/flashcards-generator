from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flashcards_generator.delivery.companion.profiles import (
    NotebookLMProfiles,
)
from flashcards_generator.services.workflows import ApplicationWorkflows


def profiles(tmp_path: Path) -> NotebookLMProfiles:
    return NotebookLMProfiles(
        tmp_path,
        lambda _home: MagicMock(spec=ApplicationWorkflows),
    )


def test_profiles_are_separate_per_user_and_private(tmp_path: Path) -> None:
    registry = profiles(tmp_path)
    first = registry.for_user("a" * 32)

    assert registry.for_user("a" * 32) is first
    assert registry.for_user("b" * 32) is not first
    assert stat.S_IMODE((tmp_path / "profiles").stat().st_mode) == 0o700
    assert (
        stat.S_IMODE((tmp_path / "profiles" / ("a" * 32)).stat().st_mode)
        == 0o700
    )
    assert (
        stat.S_IMODE((tmp_path / "profiles" / ("b" * 32)).stat().st_mode)
        == 0o700
    )


@pytest.mark.parametrize("user_id", ["../outside", "A" * 32, "short"])
def test_profile_identity_rejects_path_data(
    tmp_path: Path, user_id: str
) -> None:
    with pytest.raises(ValueError, match="invalid local profile identity"):
        profiles(tmp_path).for_user(user_id)


def test_profiles_reject_a_symlinked_profile_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "profiles").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="not a directory"):
        profiles(tmp_path).for_user("a" * 32)
