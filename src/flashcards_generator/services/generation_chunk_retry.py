from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.services.contracts import (
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.generation_models import (
    CHUNK_RETRY_BACKOFF_MULTIPLIER,
    CHUNK_RETRY_INITIAL_DELAY,
    CHUNK_RETRY_MAX_ATTEMPTS,
    CHUNK_RETRY_MAX_DELAY,
    _ChunkAttemptResult,
    _ChunkTask,
)

logger = logging.getLogger("use_cases")


class ChunkRetryContext(Protocol):
    _last_chunk_error_message: str | None

    def _process_chunk_with_retry(self, task: _ChunkTask) -> Deck | None: ...

    def _process_chunk_attempt(
        self, task: _ChunkTask, attempt: int, delay_seconds: float
    ) -> _ChunkAttemptResult: ...

    def _retry_after_chunk_error(
        self,
        error: OSError | RuntimeError,
        attempt: int,
        delay: float,
        chunk_index: int,
        total_chunks: int,
    ) -> float | None: ...

    def _wait_for_chunk_retry(
        self,
        attempt: int,
        delay: float,
        chunk_index: int,
        total_chunks: int,
        reason: str,
    ) -> float | None: ...

    def _is_transient_chunk_error(
        self, error: OSError | RuntimeError
    ) -> bool: ...

    def _process_chunk_internal(self, task: _ChunkTask) -> Deck | None: ...

    def _publish(
        self,
        stage: ProgressStage,
        state: ProgressState,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
        source: Path | None = None,
        chunk_index: int | None = None,
        cards: int | None = None,
    ) -> None: ...

    def _wait_or_cancel(self, timeout: float) -> None: ...


def process_chunk(
    context: ChunkRetryContext,
    chunk_path: Path,
    deck_name: str,
    pdf_output_path: Path,
    request: GenerateFlashcardsRequest,
    chunk_index: int,
    total_chunks: int,
) -> Deck | None:
    return context._process_chunk_with_retry(
        _ChunkTask(
            chunk_path=chunk_path,
            deck_name=deck_name,
            pdf_output_path=pdf_output_path,
            request=request,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
    )


def process_chunk_with_retry(
    context: ChunkRetryContext, task: _ChunkTask
) -> Deck | None:
    """Process a chunk with bounded exponential retry."""
    context._last_chunk_error_message = None
    attempt_result = context._process_chunk_attempt(
        task, 1, float(CHUNK_RETRY_INITIAL_DELAY)
    )
    for attempt in range(2, CHUNK_RETRY_MAX_ATTEMPTS + 1):
        if (
            attempt_result.deck is not None
            or attempt_result.next_delay_seconds is None
        ):
            break
        attempt_result = context._process_chunk_attempt(
            task, attempt, attempt_result.next_delay_seconds
        )
    if attempt_result.deck is not None:
        context._last_chunk_error_message = None
    return attempt_result.deck


def process_chunk_attempt(
    context: ChunkRetryContext,
    task: _ChunkTask,
    attempt: int,
    delay_seconds: float,
) -> _ChunkAttemptResult:
    try:
        result = context._process_chunk_internal(task)
    except (OSError, RuntimeError) as error:
        next_delay_seconds = context._retry_after_chunk_error(
            error,
            attempt,
            delay_seconds,
            task.chunk_index,
            task.total_chunks,
        )
        return _ChunkAttemptResult(None, next_delay_seconds)

    if result is not None:
        return _ChunkAttemptResult(result, None)

    next_delay_seconds = context._wait_for_chunk_retry(
        attempt,
        delay_seconds,
        task.chunk_index,
        task.total_chunks,
        "retry",
    )
    if next_delay_seconds is None:
        context._last_chunk_error_message = (
            "Chunk processing returned no result after retries"
        )
    return _ChunkAttemptResult(None, next_delay_seconds)


def retry_after_chunk_error(
    context: ChunkRetryContext,
    error: OSError | RuntimeError,
    attempt: int,
    delay: float,
    chunk_index: int,
    total_chunks: int,
) -> float | None:
    """Retry transient chunk errors and return the next delay."""
    context._last_chunk_error_message = str(error)
    if not context._is_transient_chunk_error(error):
        logger.error(f"Chunk {chunk_index}/{total_chunks}: {error}")
        return None

    next_delay = context._wait_for_chunk_retry(
        attempt,
        delay,
        chunk_index,
        total_chunks,
        "transient error, retry",
    )
    if next_delay is None:
        logger.error(f"Chunk {chunk_index}/{total_chunks}: {error}")
    return next_delay


def is_transient_chunk_error(error: OSError | RuntimeError) -> bool:
    """Return whether a chunk error is safe to retry."""
    if isinstance(error, OSError):
        return True
    error_message = str(error).lower()
    return any(
        pattern in error_message
        for pattern in (
            "rpc create_artifact",
            "rate limit",
            "generation_failed",
        )
    )


def wait_for_chunk_retry(
    context: ChunkRetryContext,
    attempt: int,
    delay: float,
    chunk_index: int,
    total_chunks: int,
    reason: str,
) -> float | None:
    """Wait before a retry, returning its bounded next delay."""
    if attempt >= CHUNK_RETRY_MAX_ATTEMPTS:
        return None
    logger.warning(
        f"Chunk {chunk_index}/{total_chunks}: {reason} "
        f"{attempt}/{CHUNK_RETRY_MAX_ATTEMPTS} in {delay}s..."
    )
    context._publish(
        ProgressStage.CHUNK,
        ProgressState.RETRYING,
        "Retrying chunk processing",
        current=attempt,
        total=CHUNK_RETRY_MAX_ATTEMPTS,
        chunk_index=chunk_index,
    )
    context._wait_or_cancel(delay)
    return min(
        delay * CHUNK_RETRY_BACKOFF_MULTIPLIER,
        CHUNK_RETRY_MAX_DELAY,
    )
