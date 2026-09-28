"""Domain-facing cancellation capability for long-running ports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class CancellationPort(Protocol):
    """Minimal cancellation API required by external-service ports."""

    def register(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback invoked when cancellation is requested."""
        ...

    def raise_if_cancelled(self) -> None:
        """Raise the domain cancellation error when cancellation is active."""
        ...

    def wait_or_cancel(self, timeout: float) -> None:
        """Wait for cancellation or return after the requested interval."""
        ...
