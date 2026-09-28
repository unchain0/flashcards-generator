import hashlib
import os
import stat
from pathlib import Path

import pytest

from flashcards_generator.integrations import source_snapshot


def _prepare_paths(root: Path, suffix: str = ".pdf") -> tuple[Path, Path]:
    input_dir = root / "input"
    input_dir.mkdir()
    output_path = root / "output" / "source"
    output_path.mkdir(parents=True)
    return input_dir / f"source{suffix}", output_path


def _track_opens(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    descriptors: list[int] = []
    open_file = os.open

    def track_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = open_file(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(source_snapshot.os, "open", track_open)
    return descriptors


def _track_closes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    descriptors: list[int] = []
    close_file = os.close

    def track_close(descriptor: int) -> None:
        descriptors.append(descriptor)
        close_file(descriptor)

    monkeypatch.setattr(source_snapshot.os, "close", track_close)
    return descriptors


def _assert_closed(descriptors: list[int]) -> None:
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("suffix", [".pdf", ".PPTX"])
def test_snapshot_copies_large_source_with_private_modes_and_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    source_path, output_path = _prepare_paths(tmp_path, suffix)
    content = bytes(range(256)) * 8193
    source_path.write_bytes(content)
    descriptors = _track_opens(monkeypatch)
    closed_descriptors = _track_closes(monkeypatch)

    snapshot_path = source_snapshot.create_source_snapshot(
        source_path, output_path
    )

    assert snapshot_path.read_bytes() == content
    assert snapshot_path.suffix == suffix.lower()
    assert stat.S_IMODE(snapshot_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(snapshot_path.parent.stat().st_mode) == 0o700
    expected_signature = f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert source_snapshot.compute_source_signature(snapshot_path) == (
        expected_signature
    )
    assert len(descriptors) == 3
    assert sorted(closed_descriptors) == sorted(descriptors)
    assert len(set(closed_descriptors)) == len(closed_descriptors)
    _assert_closed(descriptors)
    source_snapshot.cleanup_source_snapshot(snapshot_path)
    assert not snapshot_path.parent.exists()


@pytest.mark.parametrize("source_kind", ["empty", "directory", "symlink"])
def test_snapshot_rejects_invalid_source_and_closes_acquired_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    if source_kind == "empty":
        source_path.write_bytes(b"")
    elif source_kind == "directory":
        source_path.mkdir()
    else:
        target = tmp_path / "target.pdf"
        target.write_bytes(b"content")
        source_path.symlink_to(target)
    descriptors = _track_opens(monkeypatch)

    with pytest.raises(OSError):
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert not (output_path / ".flashcards_sources").exists()
    _assert_closed(descriptors)


def test_snapshot_rejects_symlinked_snapshot_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (output_path / ".flashcards_sources").symlink_to(
        outside_dir, target_is_directory=True
    )
    descriptors = _track_opens(monkeypatch)

    with pytest.raises(OSError):
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert list(outside_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_rejects_snapshot_directory_outside_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, output_path = _prepare_paths(tmp_path)
    snapshot_dir = output_path / ".flashcards_sources"
    snapshot_dir.mkdir()
    resolve_path = Path.resolve

    def escape_snapshot(path: Path, *, strict: bool = False) -> Path:
        if path == snapshot_dir:
            return tmp_path / "outside"
        return resolve_path(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", escape_snapshot)

    with pytest.raises(OSError, match="escaped output root"):
        source_snapshot._open_snapshot_directory(snapshot_dir, output_path)


@pytest.mark.parametrize("snapshot_entry", ["symlink", "file"])
def test_snapshot_rejects_unsafe_snapshot_directory_entry(
    tmp_path: Path, snapshot_entry: str
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    snapshot_dir = output_path / ".flashcards_sources"
    if snapshot_entry == "symlink":
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        snapshot_dir.symlink_to(outside_dir, target_is_directory=True)
    else:
        snapshot_dir.write_bytes(b"not a directory")

    with pytest.raises(OSError):
        source_snapshot.create_source_snapshot(source_path, output_path)


def test_snapshot_retries_name_collision_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    snapshot_dir = output_path / ".flashcards_sources"
    snapshot_dir.mkdir()
    collision = snapshot_dir / ".source.first.pdf"
    collision.write_bytes(b"preserve")
    tokens = iter(["first", "second"])
    monkeypatch.setattr(
        source_snapshot.secrets, "token_hex", lambda _: next(tokens)
    )

    snapshot_path = source_snapshot.create_source_snapshot(
        source_path, output_path
    )

    assert snapshot_path.name == ".source.second.pdf"
    assert snapshot_path.read_bytes() == b"content"
    assert collision.read_bytes() == b"preserve"
    source_snapshot.cleanup_source_snapshot(snapshot_path)
    assert collision.read_bytes() == b"preserve"


def test_snapshot_stops_after_three_collisions_without_replacing_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    snapshot_dir = output_path / ".flashcards_sources"
    snapshot_dir.mkdir()
    collision = snapshot_dir / ".source.same.pdf"
    collision.write_bytes(b"preserve")
    monkeypatch.setattr(source_snapshot.secrets, "token_hex", lambda _: "same")
    descriptors = _track_opens(monkeypatch)

    with pytest.raises(OSError, match="unique source snapshot"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert collision.read_bytes() == b"preserve"
    assert list(snapshot_dir.iterdir()) == [collision]
    _assert_closed(descriptors)


def test_snapshot_source_open_failure_propagates_without_leaking_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")

    def fail_open(*args: object, **kwargs: object) -> int:
        raise PermissionError("source open failed")

    monkeypatch.setattr(source_snapshot.os, "open", fail_open)

    with pytest.raises(PermissionError, match="source open failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)


def test_snapshot_fchmod_failure_closes_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    directory_descriptor: int | None = None
    fchmod_descriptor: int | None = None

    def fail_fchmod(descriptor: int, mode: int) -> None:
        nonlocal directory_descriptor, fchmod_descriptor
        directory_descriptor = descriptor
        fchmod_descriptor = descriptor
        raise OSError("chmod failed")

    monkeypatch.setattr(source_snapshot.os, "fchmod", fail_fchmod)

    with pytest.raises(OSError, match="chmod failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert directory_descriptor is not None
    assert fchmod_descriptor == directory_descriptor
    _assert_closed(descriptors)


def test_snapshot_fchmod_error_remains_primary_when_close_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors: list[int] = []
    source_descriptor: int | None = None
    original_close = os.close
    directory_descriptor: int | None = None
    original_open = os.open

    def capture_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal directory_descriptor, source_descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        if flags & os.O_DIRECTORY:
            directory_descriptor = descriptor
        elif source_descriptor is None:
            source_descriptor = descriptor
        return descriptor

    def fail_fchmod(descriptor: int, mode: int) -> None:
        nonlocal directory_descriptor
        directory_descriptor = descriptor
        raise OSError("chmod failed")

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        if descriptor in (source_descriptor, directory_descriptor):
            raise OSError("close failed")

    monkeypatch.setattr(source_snapshot.os, "fchmod", fail_fchmod)
    monkeypatch.setattr(source_snapshot.os, "open", capture_open)
    monkeypatch.setattr(source_snapshot.os, "close", close_then_fail)

    with pytest.raises(OSError, match="chmod failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    _assert_closed(descriptors)


def test_snapshot_directory_close_failure_removes_completed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors: list[int] = []
    original_open = os.open
    original_close = os.close
    directory_descriptor: int | None = None

    def capture_directory_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal directory_descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        if flags & os.O_DIRECTORY:
            directory_descriptor = descriptor
        return descriptor

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        if descriptor == directory_descriptor:
            raise OSError("directory close failed")

    monkeypatch.setattr(source_snapshot.os, "open", capture_directory_open)
    monkeypatch.setattr(source_snapshot.os, "close", close_then_fail)

    with pytest.raises(OSError, match="directory close failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_copy_descriptor_close_failure_remains_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors: list[int] = []
    source_descriptor: int | None = None
    original_open = os.open
    original_close = os.close

    def capture_source_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal source_descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        if source_descriptor is None:
            source_descriptor = descriptor
        return descriptor

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        if descriptor == source_descriptor:
            raise OSError("source close failed")

    monkeypatch.setattr(source_snapshot.os, "open", capture_source_open)
    monkeypatch.setattr(source_snapshot.os, "close", close_then_fail)

    with pytest.raises(OSError, match="source close failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_cleanup_failure_does_not_mask_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    snapshot_dir = output_path / ".flashcards_sources"
    unlink = Path.unlink

    def fail_snapshot_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.parent == snapshot_dir:
            raise OSError("unlink failed")
        unlink(path, missing_ok=missing_ok)

    def fail_fsync(descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(Path, "unlink", fail_snapshot_unlink)
    monkeypatch.setattr(source_snapshot.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    leftovers = list(snapshot_dir.iterdir())
    assert len(leftovers) == 1
    _assert_closed(descriptors)
    monkeypatch.setattr(Path, "unlink", unlink)
    leftovers[0].unlink()


def test_snapshot_stream_setup_failure_removes_partial_file_and_closes_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    open_stream = os.fdopen

    def fail_output_stream(descriptor: int, *args: object, **kwargs: object):
        if args and args[0] == "wb":
            raise OSError("stream setup failed")
        return open_stream(descriptor, *args, **kwargs)

    monkeypatch.setattr(source_snapshot.os, "fdopen", fail_output_stream)

    with pytest.raises(OSError, match="stream setup failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_copy_failure_removes_partial_file_and_closes_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    open_stream = os.fdopen

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self.stream.__exit__(exc_type, exc_value, traceback)

        def write(self, content: bytes) -> None:
            raise OSError("copy failed")

    def fail_write(descriptor: int, *args: object, **kwargs: object):
        stream = open_stream(descriptor, *args, **kwargs)
        if args and args[0] == "wb":
            return FailingWriter(stream)
        return stream

    monkeypatch.setattr(source_snapshot.os, "fdopen", fail_write)

    with pytest.raises(OSError, match="copy failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_copy_error_is_preserved_when_stream_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    closed_descriptors = _track_closes(monkeypatch)
    open_stream = os.fdopen
    copy_error = OSError("copy write failed")
    close_error = OSError("SECRET_OUTPUT_CLOSE_DETAIL")
    exited_modes: list[str] = []

    class StreamProxy:
        def __init__(self, stream, mode: str) -> None:
            self.stream = stream
            self.mode = mode

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.close()
            return False

        def close(self) -> None:
            exited_modes.append(self.mode)
            self.stream.close()
            if self.mode == "wb":
                raise close_error

        def write(self, content: bytes) -> None:
            raise copy_error

        def __getattr__(self, name: str):
            return getattr(self.stream, name)

    def wrap_stream(descriptor: int, *args: object, **kwargs: object):
        stream = open_stream(descriptor, *args, **kwargs)
        return StreamProxy(stream, args[0])

    monkeypatch.setattr(source_snapshot.os, "fdopen", wrap_stream)

    with pytest.raises(OSError) as result:
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert result.value is copy_error
    assert any("OSError" in note for note in copy_error.__notes__)
    assert all(
        "SECRET_OUTPUT_CLOSE_DETAIL" not in note
        for note in copy_error.__notes__
    )
    assert exited_modes == ["wb", "rb"]
    assert sorted(closed_descriptors) == sorted(descriptors)
    assert len(set(closed_descriptors)) == len(closed_descriptors)
    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_stream_setup_error_is_preserved_when_source_stream_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    closed_descriptors = _track_closes(monkeypatch)
    open_stream = os.fdopen
    setup_error = OSError("output stream setup failed")
    close_error = OSError("SECRET_SOURCE_CLOSE_DETAIL")
    exited_modes: list[str] = []

    class SourceStreamProxy:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.close()

        def close(self) -> None:
            exited_modes.append("rb")
            self.stream.close()
            raise close_error

        def __getattr__(self, name: str):
            return getattr(self.stream, name)

    def fail_second_stream(descriptor: int, *args: object, **kwargs: object):
        if args[0] == "wb":
            raise setup_error
        return SourceStreamProxy(open_stream(descriptor, *args, **kwargs))

    monkeypatch.setattr(source_snapshot.os, "fdopen", fail_second_stream)

    with pytest.raises(OSError) as result:
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert result.value is setup_error
    assert any("OSError" in note for note in setup_error.__notes__)
    assert all(
        "SECRET_SOURCE_CLOSE_DETAIL" not in note
        for note in setup_error.__notes__
    )
    assert exited_modes == ["rb"]
    assert sorted(closed_descriptors) == sorted(descriptors)
    assert len(set(closed_descriptors)) == len(closed_descriptors)
    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_stream_close_failure_propagates_without_prior_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    closed_descriptors = _track_closes(monkeypatch)
    open_stream = os.fdopen
    close_error = OSError("output stream close failed")
    exited_modes: list[str] = []

    class StreamProxy:
        def __init__(self, stream, mode: str) -> None:
            self.stream = stream
            self.mode = mode

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.close()
            return False

        def close(self) -> None:
            exited_modes.append(self.mode)
            self.stream.close()
            if self.mode == "wb":
                raise close_error

        def __getattr__(self, name: str):
            return getattr(self.stream, name)

    def fail_close(descriptor: int, *args: object, **kwargs: object):
        return StreamProxy(open_stream(descriptor, *args, **kwargs), args[0])

    monkeypatch.setattr(source_snapshot.os, "fdopen", fail_close)

    with pytest.raises(OSError) as result:
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert result.value is close_error
    assert exited_modes == ["wb", "rb"]
    assert sorted(closed_descriptors) == sorted(descriptors)
    assert len(set(closed_descriptors)) == len(closed_descriptors)
    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


@pytest.mark.parametrize("has_primary_error", [False, True])
def test_snapshot_preserves_first_close_error_and_sanitizes_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_primary_error: bool,
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors = _track_opens(monkeypatch)
    closed_descriptors = _track_closes(monkeypatch)
    open_stream = os.fdopen
    primary_error = OSError("SECRET_COPY_DETAIL")
    output_close_error = OSError("SECRET_OUTPUT_CLOSE_DETAIL")
    source_close_error = OSError("SECRET_SOURCE_CLOSE_DETAIL")
    close_errors = {"wb": output_close_error, "rb": source_close_error}
    closed_modes: list[str] = []

    class StreamProxy:
        def __init__(self, stream, mode: str) -> None:
            self.stream = stream
            self.mode = mode

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.close()
            return False

        def close(self) -> None:
            closed_modes.append(self.mode)
            self.stream.close()
            raise close_errors[self.mode]

        def write(self, content: bytes) -> None:
            if has_primary_error:
                raise primary_error
            self.stream.write(content)

        def __getattr__(self, name: str):
            return getattr(self.stream, name)

    def wrap_stream(descriptor: int, *args: object, **kwargs: object):
        return StreamProxy(open_stream(descriptor, *args, **kwargs), args[0])

    monkeypatch.setattr(source_snapshot.os, "fdopen", wrap_stream)

    with pytest.raises(OSError) as result:
        source_snapshot.create_source_snapshot(source_path, output_path)

    if has_primary_error:
        assert result.value is primary_error
        notes = primary_error.__notes__
    else:
        assert result.value is output_close_error
        notes = output_close_error.__notes__

    assert len(notes) == 2 if has_primary_error else len(notes) == 1
    assert all("OSError" in note for note in notes)
    assert all("SECRET_" not in note for note in notes)
    assert closed_modes == ["wb", "rb"]
    assert sorted(closed_descriptors) == sorted(descriptors)
    assert len(set(closed_descriptors)) == len(closed_descriptors)
    assert list((output_path / ".flashcards_sources").iterdir()) == []
    _assert_closed(descriptors)


def test_snapshot_fsync_error_remains_primary_when_descriptor_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, output_path = _prepare_paths(tmp_path)
    source_path.write_bytes(b"content")
    descriptors: list[int] = []
    original_close = os.close
    source_descriptor: int | None = None
    original_open = os.open

    def capture_source_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal source_descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        if source_descriptor is None:
            source_descriptor = descriptor
        return descriptor

    def fail_fsync(descriptor: int) -> None:
        raise OSError("fsync failed")

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        if descriptor == source_descriptor:
            raise OSError("close failed")

    monkeypatch.setattr(source_snapshot.os, "open", capture_source_open)
    monkeypatch.setattr(source_snapshot.os, "fsync", fail_fsync)
    monkeypatch.setattr(source_snapshot.os, "close", close_then_fail)

    with pytest.raises(OSError, match="fsync failed"):
        source_snapshot.create_source_snapshot(source_path, output_path)

    assert descriptors
    snapshot_dir = output_path / ".flashcards_sources"
    assert list(snapshot_dir.iterdir()) == []
    _assert_closed(descriptors)


def test_cleanup_removes_snapshot_but_preserves_nonempty_directory(
    tmp_path: Path,
) -> None:
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    snapshot_path = snapshot_dir / "source.pdf"
    snapshot_path.write_bytes(b"temporary")
    other_file = snapshot_dir / "keep.txt"
    other_file.write_text("keep")

    source_snapshot.cleanup_source_snapshot(snapshot_path)
    source_snapshot.cleanup_source_snapshot(snapshot_dir / "missing.pdf")

    assert not snapshot_path.exists()
    assert other_file.read_text() == "keep"
    assert snapshot_dir.is_dir()

    other_file.unlink()
    source_snapshot.cleanup_source_snapshot(snapshot_dir / "missing.pdf")
    assert not snapshot_dir.exists()
