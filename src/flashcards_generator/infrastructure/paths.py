import shutil
from pathlib import Path


def find_notebooklm() -> str:
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
