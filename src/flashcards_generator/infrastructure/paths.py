"""Path utilities for finding external tools."""

import shutil
from pathlib import Path


def find_notebooklm() -> str:
    """Find the NotebookLM executable path.

    Searches for the notebooklm executable in the following order:
    1. System PATH
    2. UV tools installation directory
    3. Local bin directory

    Returns:
        str: Path to the notebooklm executable, or "notebooklm" if not found.
    """
    notebooklm_path = shutil.which("notebooklm")
    if notebooklm_path:
        return notebooklm_path

    uv_paths = [
        Path.home()
        / ".local"
        / "share"
        / "uv"
        / "tools"
        / "notebooklm-py"
        / "bin"
        / "notebooklm",
        Path.home() / ".local" / "bin" / "notebooklm",
    ]

    for path in uv_paths:
        if path.exists():
            return str(path)

    return "notebooklm"
