"""Path utilities for finding external tools."""

import shutil


def find_notebooklm() -> str:
    """Find the NotebookLM executable path.

    Searches for the notebooklm executable in the following order:
    1. System PATH
    2. The command name as a final fallback

    Returns:
        str: Path to the notebooklm executable, or "notebooklm" if not found.
    """
    return shutil.which("notebooklm") or "notebooklm"
