"""Deterministic tests for cooperative cancellation."""

from threading import Event, Thread

import pytest

from flashcards_generator.application.contracts import CancellationToken
from flashcards_generator.domain.exceptions import (
    FlashcardsGeneratorError,
    OperationCancelled,
)


def test_operation_cancelled_is_distinct_from_runtime_error() -> None:
    error = OperationCancelled()

    assert isinstance(error, FlashcardsGeneratorError)
    assert RuntimeError not in type(error).__mro__


def test_cancel_sets_state_and_raise_if_cancelled_uses_domain_exception() -> (
    None
):
    token = CancellationToken()

    assert token.is_cancelled is False
    token.raise_if_cancelled()
    token.cancel()

    assert token.is_cancelled is True
    with pytest.raises(OperationCancelled):
        token.raise_if_cancelled()


def test_cancel_invokes_each_registration_once() -> None:
    token = CancellationToken()
    invocations = 0

    def callback() -> None:
        nonlocal invocations
        invocations += 1

    token.register(callback)

    token.cancel()
    token.cancel()

    assert invocations == 1


def test_unregister_is_idempotent_and_prevents_callback() -> None:
    token = CancellationToken()
    callback_called = Event()
    unregister = token.register(callback_called.set)

    unregister()
    unregister()
    token.cancel()

    assert callback_called.is_set() is False


def test_register_after_cancellation_invokes_callback_immediately() -> None:
    token = CancellationToken()
    callback_called = Event()
    token.cancel()

    unregister = token.register(callback_called.set)
    unregister()

    assert callback_called.is_set() is True


def test_wait_or_cancel_returns_after_timeout_when_active() -> None:
    token = CancellationToken()

    token.wait_or_cancel(0)


def test_wait_or_cancel_is_interrupted_by_cancellation() -> None:
    token = CancellationToken()
    wait_started = Event()
    finished = Event()
    raised: list[OperationCancelled] = []

    def wait() -> None:
        wait_started.set()
        try:
            token.wait_or_cancel(60)
        except OperationCancelled as error:
            raised.append(error)
        finally:
            finished.set()

    thread = Thread(target=wait)
    thread.start()
    assert wait_started.wait(timeout=1)
    token.cancel()
    assert finished.wait(timeout=1)
    thread.join()

    assert len(raised) == 1
    assert isinstance(raised[0], OperationCancelled)
