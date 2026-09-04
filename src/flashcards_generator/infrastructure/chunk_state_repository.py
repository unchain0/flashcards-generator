"""Filesystem-backed repository for chunk resume state."""

from __future__ import annotations

import fcntl
import os
import secrets
import shutil
import stat
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from flashcards_generator.domain.entities import ChunkResumeManifest, Deck
from flashcards_generator.domain.ports import ChunkStatePort
from flashcards_generator.infrastructure.logging_config import get_logger

logger = get_logger("chunk_state_repository")

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class FileSystemChunkStateRepository(ChunkStatePort):
    """Persist private chunk manifests and result decks as JSON files."""

    def load_manifest(self, state_path: Path) -> ChunkResumeManifest | None:
        """Load a chunk resume manifest from disk if present."""
        try:
            content = self._read_text(state_path)
        except FileNotFoundError:
            logger.debug(f"Manifest not found: {state_path}")
            return None

        logger.debug(f"Loading manifest from {state_path}")
        return ChunkResumeManifest.model_validate_json(content)

    def save_manifest(
        self, state_path: Path, manifest: ChunkResumeManifest
    ) -> None:
        """Persist a chunk resume manifest with a durable atomic replacement."""
        logger.debug(f"Saving manifest to {state_path}")
        self._atomic_write(state_path, manifest.model_dump_json(indent=2))

    def delete_manifest(self, state_path: Path) -> None:
        """Delete a persisted manifest without following a symlink."""
        try:
            state_path.unlink()
        except FileNotFoundError:
            return
        logger.debug(f"Deleting manifest at {state_path}")

    def save_chunk_result(self, path: Path, deck: Deck) -> None:
        """Persist an individual chunk result deck atomically."""
        logger.debug(f"Saving chunk result to {path}")
        self._atomic_write(path, deck.model_dump_json(indent=2))

    def load_chunk_result(self, path: Path) -> Deck:
        """Load an individual chunk result deck without following symlinks."""
        logger.debug(f"Loading chunk result from {path}")
        return Deck.model_validate_json(self._read_text(path))

    def delete_chunk_results(self, dir_path: Path) -> None:
        """Delete only the requested result directory."""
        try:
            mode = dir_path.lstat().st_mode
        except FileNotFoundError:
            return

        if stat.S_ISLNK(mode):
            dir_path.unlink()
        else:
            logger.debug(f"Deleting chunk results directory {dir_path}")
            shutil.rmtree(dir_path)

    @contextmanager
    def resume_lock(self, resume_dir: Path) -> Generator[bool, None, None]:
        """Acquire non-blocking exclusive ownership of a resume directory."""
        self._ensure_private_directory(resume_dir.parent)
        directory_fd = self._open_directory(resume_dir.parent)
        lock_fd: int | None = None
        try:
            lock_fd = os.open(
                f".{resume_dir.name}.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                _FILE_MODE,
                dir_fd=directory_fd,
            )
            os.fchmod(lock_fd, _FILE_MODE)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return

            yield True
        finally:
            if lock_fd is not None:
                with suppress_os_error():
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            os.close(directory_fd)

    def _read_text(self, path: Path) -> str:
        self._assert_no_symlink_parents(path.parent)
        flags = os.O_RDONLY | os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"State path is not a regular file: {path}")
            with os.fdopen(fd, encoding="utf-8") as file_obj:
                fd = -1
                return file_obj.read()
        finally:
            if fd != -1:
                os.close(fd)

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content with a unique, private sibling temporary file."""
        self._ensure_private_directory(path.parent)
        directory_fd = self._open_directory(path.parent)
        temp_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
        temp_fd: int | None = None
        try:
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _FILE_MODE,
                dir_fd=directory_fd,
            )
            os.fchmod(temp_fd, _FILE_MODE)
            with os.fdopen(temp_fd, "w", encoding="utf-8") as file_obj:
                temp_fd = None
                file_obj.write(content)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(
                temp_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        except Exception:
            if temp_fd is not None:
                os.close(temp_fd)
            with suppress_os_error():
                os.unlink(temp_name, dir_fd=directory_fd)
            raise
        finally:
            os.close(directory_fd)

    def _ensure_private_directory(self, directory: Path) -> None:
        current = Path(directory.anchor)
        parts = (
            directory.parts[1:] if directory.is_absolute() else directory.parts
        )
        for part in parts:
            current /= part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                current.mkdir(mode=_DIRECTORY_MODE)
                mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise OSError(
                    f"State directory is not a real directory: {current}"
                )
        os.chmod(directory, _DIRECTORY_MODE)

    def _assert_no_symlink_parents(self, directory: Path) -> None:
        current = Path(directory.anchor)
        parts = (
            directory.parts[1:] if directory.is_absolute() else directory.parts
        )
        for part in parts:
            current /= part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise OSError(
                    f"State directory is not a real directory: {current}"
                )

    @staticmethod
    def _open_directory(directory: Path) -> int:
        return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


@contextmanager
def suppress_os_error() -> Iterator[None]:
    try:
        yield
    except OSError:
        pass
