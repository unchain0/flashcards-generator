"""Failing-first real-PTY coverage for the Textual surface."""

from __future__ import annotations

import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_until(fd: int, marker: bytes, timeout: float = 5) -> bytes:
    """Read PTY output until a marker or the bounded deadline."""
    deadline = time.monotonic() + timeout
    output = bytearray()
    while time.monotonic() < deadline:
        ready, _, _ = select.select(
            [fd], [], [], max(0.0, deadline - time.monotonic())
        )
        if not ready:
            break
        try:
            output.extend(os.read(fd, 4096))
        except OSError:
            break
        if marker and marker in output:
            break
    return bytes(output)


@pytest.mark.parametrize("columns, rows", [(120, 40), (52, 24)])
def test_primary_tui_renders_real_workflow_surface_in_pty(
    columns: int,
    rows: int,
) -> None:
    """Given a real terminal, the workflow surface fits and is usable."""
    master_fd, slave_fd = pty.openpty()
    environment = os.environ.copy()
    environment["TERM"] = "xterm-256color"
    process = subprocess.Popen(
        ["uv", "run", "flashcards"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)

    exit_code: int | None = None
    try:
        window = struct.pack("HHHH", rows, columns, 0, 0)
        termios.tcsetattr(
            master_fd,
            termios.TCSANOW,
            termios.tcgetattr(master_fd),
        )
        import fcntl

        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, window)
        output = _read_until(master_fd, b"q Q")
        os.write(master_fd, b"q")
        output += _read_until(master_fd, b"", timeout=3)
        exit_code = process.wait(timeout=5)
    finally:
        if exit_code is None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        os.close(master_fd)

    decoded = output.decode("utf-8", errors="replace")
    assert (
        exit_code,
        "Input directory" in decoded,
        "Placeholder" not in decoded,
    ) == (0, True, True)
