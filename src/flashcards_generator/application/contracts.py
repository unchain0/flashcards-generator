"""Framework-neutral progress, outcome, and cancellation contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from typing import Protocol

from flashcards_generator.domain.entities import Deck
from flashcards_generator.domain.exceptions import OperationCancelled

CancellationCallback = Callable[[], None]
UnregisterCallback = Callable[[], None]


class ProgressStage(str, Enum):
    """Stage of an application operation."""

    AUTH = "auth"
    DISCOVERY = "discovery"
    SOURCE = "source"
    CHUNK = "chunk"
    GENERATION = "generation"
    DOWNLOAD = "download"
    EXPORT = "export"
    CLEANUP = "cleanup"
    MERGE = "merge"


class ProgressState(str, Enum):
    """State transition within a progress stage."""

    STARTED = "started"
    ADVANCED = "advanced"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One immutable application progress update."""

    stage: ProgressStage
    state: ProgressState
    message: str
    current: int | None = None
    total: int | None = None
    source: Path | None = None
    chunk_index: int | None = None
    cards: int | None = None


class ProgressReporter(Protocol):
    """Consumer of application progress updates."""

    def publish(self, event: ProgressEvent) -> None:
        """Publish one progress update."""
        ...


class NullProgressReporter:
    """Progress reporter that intentionally discards updates."""

    def publish(self, event: ProgressEvent) -> None:
        """Discard one progress update."""


@dataclass(frozen=True, slots=True)
class SourceFailure:
    """Failure associated with one discovered source."""

    source: Path
    reason: str


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """Immutable result of a generation operation."""

    decks: tuple[Deck, ...]
    discovered_sources: int
    completed_sources: int
    skipped_sources: int
    failed_sources: tuple[SourceFailure, ...]
    csv_paths: tuple[Path, ...] = ()
    elapsed_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        """Return whether every source avoided failure."""
        return not self.failed_sources


class CancellationToken:
    """Thread-safe cooperative cancellation signal."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._callbacks: dict[int, CancellationCallback] = {}
        self._next_registration_id = 0

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation and invoke each active registration once."""
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()

        for callback in callbacks:
            callback()

    def raise_if_cancelled(self) -> None:
        """Raise the project cancellation exception when cancelled."""
        if self._event.is_set():
            raise OperationCancelled

    def wait_or_cancel(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds, raising if cancellation arrives."""
        if self._event.wait(timeout):
            raise OperationCancelled

    def register(self, callback: CancellationCallback) -> UnregisterCallback:
        """Register a callback and return an idempotent unregister callable."""
        with self._lock:
            if self._event.is_set():
                registration_id = None
            else:
                registration_id = self._next_registration_id
                self._next_registration_id += 1
                self._callbacks[registration_id] = callback

        if registration_id is None:
            callback()

        def unregister() -> None:
            if registration_id is None:
                return
            with self._lock:
                self._callbacks.pop(registration_id, None)

        return unregister
