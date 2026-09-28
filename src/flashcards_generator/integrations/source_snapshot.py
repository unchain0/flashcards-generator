from __future__ import annotations

import hashlib
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

from flashcards_generator.integrations.logging_config import get_logger

logger = get_logger("source_snapshot")

_COPY_BLOCK_SIZE = 1024 * 1024
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_SNAPSHOT_ALLOCATION_ATTEMPTS = 3


def compute_source_signature(source_path: Path) -> str:
    digest = hashlib.sha256()
    with source_path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(_COPY_BLOCK_SIZE), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def create_source_snapshot(source_path: Path, output_path: Path) -> Path:
    source_fd: int | None = None
    snapshot_dir_fd: int | None = None
    snapshot_fd: int | None = None
    snapshot_path: Path | None = None
    primary_error: BaseException | None = None

    try:
        source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
        _validate_source_fd(source_fd, source_path)

        snapshot_dir = output_path / ".flashcards_sources"
        snapshot_dir_fd = _open_snapshot_directory(snapshot_dir, output_path)
        snapshot_fd, snapshot_path = _allocate_snapshot(
            snapshot_dir_fd, snapshot_dir, source_path
        )

        source_fd_to_copy = source_fd
        snapshot_fd_to_copy = snapshot_fd
        source_fd = None
        snapshot_fd = None
        _copy_snapshot(source_fd_to_copy, snapshot_fd_to_copy)
        return snapshot_path
    except BaseException as error:
        primary_error = error
        _remove_incomplete_snapshot(snapshot_path)
        raise
    finally:
        close_errors = _close_descriptors(
            source_fd, snapshot_fd, snapshot_dir_fd
        )
        if close_errors:
            if primary_error is not None:
                _log_cleanup_errors("descriptor close", close_errors)
            else:
                _remove_incomplete_snapshot(snapshot_path)
                _log_cleanup_errors("descriptor close", close_errors[1:])
                raise close_errors[0]


def cleanup_source_snapshot(snapshot_path: Path) -> None:
    snapshot_path.unlink(missing_ok=True)
    with suppress(OSError):
        snapshot_path.parent.rmdir()


def _validate_source_fd(source_fd: int, source_path: Path) -> None:
    source_stat = os.fstat(source_fd)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size == 0:
        raise OSError(f"Input is not a nonempty regular file: {source_path}")


def _open_snapshot_directory(snapshot_dir: Path, output_path: Path) -> int:
    snapshot_dir.mkdir(mode=_DIRECTORY_MODE, exist_ok=True)

    snapshot_mode = snapshot_dir.lstat().st_mode
    if stat.S_ISLNK(snapshot_mode) or not stat.S_ISDIR(snapshot_mode):
        raise OSError(
            f"Snapshot directory is not a real directory: {snapshot_dir}"
        )

    output_root = output_path.resolve(strict=True)
    resolved_snapshot_dir = snapshot_dir.resolve(strict=True)
    if not resolved_snapshot_dir.is_relative_to(output_root):
        raise OSError(
            f"Snapshot directory escaped output root: {snapshot_dir}"
        )

    snapshot_dir_fd = os.open(
        snapshot_dir,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fchmod(snapshot_dir_fd, _DIRECTORY_MODE)
    except BaseException:
        _log_cleanup_errors(
            "snapshot directory close", _close_descriptors(snapshot_dir_fd)
        )
        raise
    return snapshot_dir_fd


def _allocate_snapshot(
    snapshot_dir_fd: int, snapshot_dir: Path, source_path: Path
) -> tuple[int, Path]:
    for _ in range(_SNAPSHOT_ALLOCATION_ATTEMPTS):
        snapshot_name = (
            f".{source_path.stem}.{secrets.token_hex(16)}"
            f"{source_path.suffix.lower()}"
        )
        try:
            snapshot_fd = os.open(
                snapshot_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _FILE_MODE,
                dir_fd=snapshot_dir_fd,
            )
            return snapshot_fd, snapshot_dir / snapshot_name
        except FileExistsError:
            continue
    raise OSError("Could not allocate a unique source snapshot")


def _copy_snapshot(source_fd: int, snapshot_fd: int) -> None:
    primary_error: BaseException | None = None
    try:
        _copy_snapshot_streams(source_fd, snapshot_fd)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        close_errors = _close_descriptors(source_fd, snapshot_fd)
        if close_errors:
            if primary_error is not None:
                _log_cleanup_errors(
                    "snapshot copy descriptor close", close_errors
                )
            else:
                _log_cleanup_errors(
                    "snapshot copy descriptor close", close_errors[1:]
                )
                raise close_errors[0]


def _copy_snapshot_streams(source_fd: int, snapshot_fd: int) -> None:
    source_context: BinaryIO | None = None
    source_file: BinaryIO | None = None
    snapshot_context: BinaryIO | None = None
    snapshot_file: BinaryIO | None = None
    try:
        source_context = os.fdopen(source_fd, "rb", closefd=False)
        source_file = source_context.__enter__()
        snapshot_context = os.fdopen(snapshot_fd, "wb", closefd=False)
        snapshot_file = snapshot_context.__enter__()
        while block := source_file.read(_COPY_BLOCK_SIZE):
            snapshot_file.write(block)
        snapshot_file.flush()
        os.fsync(snapshot_file.fileno())
    except BaseException as error:
        _close_snapshot_streams(error, snapshot_context, source_context)
        raise
    _close_snapshot_streams(None, snapshot_context, source_context)


def _close_snapshot_streams(
    primary_error: BaseException | None,
    *streams: BinaryIO | None,
) -> None:
    close_errors = _attempt_stream_closes(streams)
    if close_errors:
        _preserve_stream_close_errors(primary_error, close_errors)


def _attempt_stream_closes(
    streams: tuple[BinaryIO | None, ...],
) -> list[OSError]:
    close_errors: list[OSError] = []
    for stream in streams:
        if stream is None:
            continue
        try:
            stream.__exit__(None, None, None)
        except OSError as error:
            close_errors.append(error)
    return close_errors


def _preserve_stream_close_errors(
    primary_error: BaseException | None,
    close_errors: list[OSError],
) -> None:
    error_to_preserve = primary_error or close_errors.pop(0)

    for error in close_errors:
        error_to_preserve.add_note(
            f"Closing a snapshot stream also failed ({type(error).__name__})"
        )
    if primary_error is None:
        raise error_to_preserve


def _close_descriptors(*descriptors: int | None) -> list[OSError]:
    errors: list[OSError] = []
    for descriptor in descriptors:
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except OSError as error:
            errors.append(error)
    return errors


def _remove_incomplete_snapshot(snapshot_path: Path | None) -> None:
    if snapshot_path is None:
        return
    try:
        snapshot_path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning(
            f"Could not remove incomplete snapshot {snapshot_path}: {error}"
        )


def _log_cleanup_errors(operation: str, errors: list[OSError]) -> None:
    for error in errors:
        logger.warning(f"Failed {operation}: {error}")


class FileSystemSourceSnapshots:
    def compute_signature(self, source_path: Path) -> str:
        return compute_source_signature(source_path)

    def create(self, source_path: Path, output_path: Path) -> Path:
        return create_source_snapshot(source_path, output_path)

    def cleanup(self, snapshot_path: Path) -> None:
        cleanup_source_snapshot(snapshot_path)
