from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from flashcards_generator.services.workflows import ApplicationWorkflows

_USER_ID = re.compile(r"[a-f0-9]{32}\Z")


class NotebookLMProfiles:
    def __init__(
        self,
        data_dir: Path,
        workflow_factory: Callable[[Path], ApplicationWorkflows],
    ) -> None:
        self._profiles_dir = data_dir / "profiles"
        self._workflow_factory = workflow_factory
        self._workflows: dict[str, ApplicationWorkflows] = {}
        self._lock = Lock()

    def for_user(self, user_id: str) -> ApplicationWorkflows:
        if _USER_ID.fullmatch(user_id) is None:
            raise ValueError("invalid local profile identity")
        with self._lock:
            workflow = self._workflows.get(user_id)
            if workflow is not None:
                return workflow
            home = self._profile_home(user_id)
            workflow = self._workflow_factory(home)
            self._workflows[user_id] = workflow
            return workflow

    def _profile_home(self, user_id: str) -> Path:
        self._ensure_private_directory(self._profiles_dir)
        home = self._profiles_dir / user_id
        self._ensure_private_directory(home)
        return home

    @staticmethod
    def _ensure_private_directory(directory: Path) -> None:
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError:
            directory.mkdir(mode=0o700, parents=True)
            mode = directory.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise OSError(f"Profile path is not a directory: {directory}")
        os.chmod(directory, 0o700, follow_symlinks=False)
