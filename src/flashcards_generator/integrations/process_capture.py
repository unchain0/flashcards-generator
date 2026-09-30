"""Bounded capture of stdout and stderr from local POSIX processes."""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
from typing import IO

from flashcards_generator.integrations.document_limits import MAX_JSON_BYTES


def communicate_bounded(
    process: subprocess.Popen[str], *, timeout: float
) -> tuple[str, str]:
    try:
        if process.stdout is None or process.stderr is None:
            raise ValueError("stdout and stderr must both be captured")
        descriptors = (process.stdout.fileno(), process.stderr.fileno())
        buffers = {descriptor: bytearray() for descriptor in descriptors}
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for descriptor in descriptors:
                selector.register(descriptor, selectors.EVENT_READ)
            _read_pipes(selector, buffers, process, deadline, timeout)
        process.wait(timeout=max(0, deadline - time.monotonic()))
        return (
            _decode_output(buffers[descriptors[0]]),
            _decode_output(buffers[descriptors[1]]),
        )
    finally:
        close_process_pipes(process, sys.exception())


def _read_pipes(
    selector: selectors.BaseSelector,
    buffers: dict[int, bytearray],
    process: subprocess.Popen[str],
    deadline: float,
    timeout: float,
) -> None:
    remaining_bytes = MAX_JSON_BYTES
    while selector.get_map():
        remaining_time = _remaining_time(process, deadline, timeout)
        for key, _events in selector.select(remaining_time):
            chunk = os.read(key.fd, min(65_536, remaining_bytes + 1))
            if not chunk:
                selector.unregister(key.fd)
            elif len(chunk) > remaining_bytes:
                raise subprocess.SubprocessError(
                    f"Subprocess output exceeds {MAX_JSON_BYTES} bytes"
                )
            else:
                buffers[key.fd].extend(chunk)
                remaining_bytes -= len(chunk)


def _remaining_time(
    process: subprocess.Popen[str], deadline: float, timeout: float
) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(process.args, timeout)
    return remaining


def _decode_output(data: bytearray) -> str:
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def close_process_pipes(
    process: subprocess.Popen[str], primary_error: BaseException | None = None
) -> None:
    failure = primary_error
    for stream in (process.stdout, process.stderr):
        failure = _close_pipe(stream, failure)
    if primary_error is None and failure is not None:
        raise failure


def _close_pipe(
    stream: IO[str] | None, primary_error: BaseException | None
) -> BaseException | None:
    if stream is None:
        return primary_error
    try:
        stream.close()
    except (OSError, ValueError) as error:
        if primary_error is None:
            return error
        primary_error.add_note(
            f"Closing subprocess pipe failed ({type(error).__name__})."
        )
    return primary_error
