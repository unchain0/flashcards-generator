"""Small Linux desktop integrations used by the outer interfaces."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def copy_text(text: str) -> bool:
    """Copy text using an available Wayland or X11 clipboard command."""
    command = shutil.which("wl-copy") or shutil.which("xclip")
    if command is None:
        return False
    arguments = [command]
    if command.endswith("xclip"):
        arguments.extend(["-selection", "clipboard"])
    try:
        result = subprocess.run(
            arguments,
            input=text,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def open_path(path: Path) -> bool:
    """Ask the Linux desktop to open a path with its default application."""
    opener = shutil.which("xdg-open")
    if opener is None:
        return False
    try:
        result = subprocess.run(
            [opener, str(path)],
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
