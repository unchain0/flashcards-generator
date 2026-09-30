from __future__ import annotations

import csv
import os
import tempfile
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, assert_never

import pytest

from flashcards_generator.integrations import deck_exporter as exporter
from flashcards_generator.integrations.deck_exporter import DeckExporter


class FailureStage(StrEnum):
    CREATE = "create"
    CONVERSION = "conversion"
    FLUSH = "flush"
    FSYNC = "fsync"
    CLOSE = "close"
    REPLACE = "replace"


class TemporaryCsvFile(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def closed(self) -> bool: ...

    def write(self, value: str, /) -> int: ...

    def flush(self) -> None: ...

    def fileno(self) -> int: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> bool | None: ...


class FaultingTemporaryCsvFile:
    def __init__(
        self,
        file_obj: TemporaryCsvFile,
        failure_stage: FailureStage,
        failure: OSError,
    ) -> None:
        self._file_obj = file_obj
        self._failure_stage = failure_stage
        self._failure = failure

    @property
    def name(self) -> str:
        return self._file_obj.name

    @property
    def closed(self) -> bool:
        return self._file_obj.closed

    def write(self, value: str, /) -> int:
        return self._file_obj.write(value)

    def flush(self) -> None:
        if self._failure_stage == FailureStage.FLUSH:
            raise self._failure
        self._file_obj.flush()

    def fileno(self) -> int:
        return self._file_obj.fileno()

    def __enter__(self) -> Self:
        self._file_obj.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self._file_obj.__exit__(exc_type, exc_value, traceback)
        if self._failure_stage == FailureStage.CLOSE:
            raise self._failure
        return False


def _install_faulting_file(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: FailureStage,
    failure: OSError,
    captured_files: list[FaultingTemporaryCsvFile],
) -> None:
    original_factory = tempfile.NamedTemporaryFile

    def create_file(
        *,
        mode: str,
        newline: str,
        encoding: str,
        dir: str | os.PathLike[str],
        prefix: str,
        suffix: str,
        delete: bool,
    ) -> FaultingTemporaryCsvFile:
        file_obj = original_factory(
            mode=mode,
            newline=newline,
            encoding=encoding,
            dir=dir,
            prefix=prefix,
            suffix=suffix,
            delete=delete,
        )
        faulting_file = FaultingTemporaryCsvFile(
            file_obj, failure_stage, failure
        )
        captured_files.append(faulting_file)
        return faulting_file

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", create_file)


@pytest.mark.parametrize("destination_exists", [False, True])
@pytest.mark.parametrize(
    "failure_stage",
    [
        FailureStage.CREATE,
        FailureStage.CONVERSION,
        FailureStage.FLUSH,
        FailureStage.FSYNC,
        FailureStage.CLOSE,
        FailureStage.REPLACE,
    ],
)
def test_failed_csv_export_never_publishes_partial_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deck_with_cards,
    destination_exists: bool,
    failure_stage: FailureStage,
) -> None:
    output_path = tmp_path / "deck.csv"
    previous_bytes = b"previous export\n"
    if destination_exists:
        output_path.write_bytes(previous_bytes)

    failure = OSError(f"injected {failure_stage.value} failure")
    captured_files: list[FaultingTemporaryCsvFile] = []
    with monkeypatch.context() as patcher:
        match failure_stage:
            case FailureStage.CREATE:

                def fail_creation(
                    **_kwargs: object,
                ) -> TemporaryCsvFile:
                    raise failure

                patcher.setattr(tempfile, "NamedTemporaryFile", fail_creation)
            case FailureStage.CONVERSION:
                original_convert = exporter.convert_to_anki_math_format
                calls = 0

                def fail_after_first_row(text: str) -> str:
                    nonlocal calls
                    calls += 1
                    if calls == 3:
                        raise failure
                    return original_convert(text)

                patcher.setattr(
                    exporter,
                    "convert_to_anki_math_format",
                    fail_after_first_row,
                )
            case FailureStage.FLUSH | FailureStage.CLOSE:
                _install_faulting_file(
                    patcher, failure_stage, failure, captured_files
                )
            case FailureStage.FSYNC:

                def fail_fsync(_descriptor: int) -> None:
                    raise failure

                patcher.setattr(os, "fsync", fail_fsync)
                _install_faulting_file(
                    patcher, failure_stage, failure, captured_files
                )
            case FailureStage.REPLACE:

                def fail_replace(_source: Path, _target: Path) -> Path:
                    raise failure

                patcher.setattr(Path, "replace", fail_replace)
                _install_faulting_file(
                    patcher, failure_stage, failure, captured_files
                )
            case unreachable:
                assert_never(unreachable)

        with pytest.raises(OSError) as error:
            DeckExporter.export_csv(deck_with_cards, output_path)

    assert error.value is failure
    if destination_exists:
        assert output_path.read_bytes() == previous_bytes
    else:
        assert not output_path.exists()
    assert list(tmp_path.glob(".deck.csv.*.tmp")) == []


def test_export_cleanup_failure_is_not_allowed_to_replace_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deck_with_cards,
) -> None:
    output_path = tmp_path / "deck.csv"
    primary_failure = OSError("injected conversion failure")
    cleanup_failure = OSError("injected cleanup failure")
    captured_files: list[FaultingTemporaryCsvFile] = []
    original_unlink = Path.unlink
    original_convert = exporter.convert_to_anki_math_format
    calls = 0

    def fail_after_first_row(text: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise primary_failure
        return original_convert(text)

    def fail_temporary_cleanup(
        path: Path, *, missing_ok: bool = False
    ) -> None:
        if path.name.startswith(".deck.csv."):
            raise cleanup_failure
        original_unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patcher:
        _install_faulting_file(
            patcher, FailureStage.CONVERSION, primary_failure, captured_files
        )
        patcher.setattr(
            exporter, "convert_to_anki_math_format", fail_after_first_row
        )
        patcher.setattr(Path, "unlink", fail_temporary_cleanup)

        with pytest.raises(OSError) as error:
            DeckExporter.export_csv(deck_with_cards, output_path)

    assert error.value is primary_failure
    assert any(
        "cleanup also failed (OSError)" in note
        for note in error.value.__notes__
    )
    assert not output_path.exists()
    assert len(captured_files) == 1
    temporary_path = Path(captured_files[0].name)
    assert temporary_path.exists()
    original_unlink(temporary_path)


def test_csv_is_complete_and_closed_before_atomic_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deck_with_cards,
) -> None:
    output_path = tmp_path / "deck.csv"
    captured_files: list[FaultingTemporaryCsvFile] = []
    original_factory = tempfile.NamedTemporaryFile
    original_replace = Path.replace

    def record_file(
        *,
        mode: str,
        newline: str,
        encoding: str,
        dir: str | os.PathLike[str],
        prefix: str,
        suffix: str,
        delete: bool,
    ) -> FaultingTemporaryCsvFile:
        file_obj = original_factory(
            mode=mode,
            newline=newline,
            encoding=encoding,
            dir=dir,
            prefix=prefix,
            suffix=suffix,
            delete=delete,
        )
        captured_file = FaultingTemporaryCsvFile(
            file_obj, FailureStage.CONVERSION, OSError("unused")
        )
        captured_files.append(captured_file)
        return captured_file

    def observe_publication(source: Path, target: Path) -> Path:
        assert captured_files[0].closed
        with source.open(encoding="utf-8", newline="") as file_obj:
            rows = list(csv.reader(file_obj))
        assert rows == [
            [
                exporter.convert_to_anki_math_format(card.front),
                exporter.convert_to_anki_math_format(card.back),
            ]
            for card in deck_with_cards.flashcards
        ]
        return original_replace(source, target)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", record_file)
    monkeypatch.setattr(Path, "replace", observe_publication)

    DeckExporter.export_csv(deck_with_cards, output_path)

    assert output_path.exists()
    assert captured_files[0].closed

    assert list(tmp_path.glob(".deck.csv.*.tmp")) == []


def test_csv_export_does_not_create_a_missing_parent_directory(
    tmp_path: Path,
    deck_with_cards,
) -> None:
    output_path = tmp_path / "missing" / "deck.csv"

    with pytest.raises(FileNotFoundError):
        DeckExporter.export_csv(deck_with_cards, output_path)

    assert not output_path.parent.exists()
