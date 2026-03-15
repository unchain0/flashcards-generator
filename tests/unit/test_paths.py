import shutil
from pathlib import Path
from unittest.mock import patch

from flashcards_generator.infrastructure.paths import find_notebooklm


class TestFindNotebooklm:
    def test_find_in_path(self):
        with patch.object(shutil, "which", return_value="/usr/bin/notebooklm"):
            result = find_notebooklm()
            assert result == "/usr/bin/notebooklm"

    def test_find_in_uv_tools(self, tmp_path):
        uv_path = (
            tmp_path
            / ".local"
            / "share"
            / "uv"
            / "tools"
            / "notebooklm-py"
            / "bin"
            / "notebooklm"
        )
        uv_path.parent.mkdir(parents=True)
        uv_path.touch()
        uv_path.chmod(0o755)

        with patch.object(Path, "home", return_value=tmp_path):
            with patch.object(shutil, "which", return_value=None):
                result = find_notebooklm()
                assert result == str(uv_path)

    def test_find_in_local_bin(self, tmp_path):
        local_bin = tmp_path / ".local" / "bin" / "notebooklm"
        local_bin.parent.mkdir(parents=True)
        local_bin.touch()
        local_bin.chmod(0o755)

        with patch.object(Path, "home", return_value=tmp_path):
            with patch.object(shutil, "which", return_value=None):
                result = find_notebooklm()
                assert result == str(local_bin)

    def test_fallback_to_command_name(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            with patch.object(shutil, "which", return_value=None):
                result = find_notebooklm()
                assert result == "notebooklm"
