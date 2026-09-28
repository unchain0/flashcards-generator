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
    _ChunkRun,
    _ChunkTask,
)
from flashcards_generator.services.ports.pdf_chunker import PDFChunkerPort

logger = logging.getLogger("use_cases")


class ChunkOrchestrationContext(Protocol):
    _last_chunk_error_message: str | None

    @property
    def pdf_chunker(self) -> PDFChunkerPort: ...

    def _prepare_resume(self, run: _ChunkRun) -> None: ...

    def _process_chunks(self, run: _ChunkRun) -> bool: ...

    def _combine_chunk_decks(self, run: _ChunkRun) -> Deck | None: ...

    def _raise_if_cancelled(self) -> None: ...

    def _get_or_process_chunk(
        self, run: _ChunkRun, chunk_index: int, chunk_path: Path
    ) -> Deck | None: ...

    def _log_chunk_result(
        self, chunk_index: int, total_chunks: int, chunk_deck: Deck
    ) -> None: ...

    def _wait_or_cancel(self, timeout: float) -> None: ...

    def _process_chunk(
        self,
        chunk_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        chunk_index: int,
        total_chunks: int,
    ) -> Deck | None: ...

    def _mark_chunk_failed(self, run: _ChunkRun, chunk_index: int) -> None: ...

    def _save_chunk_completion(
        self, run: _ChunkRun, chunk_index: int, chunk_deck: Deck
    ) -> None: ...

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


def process_large_pdf(
    context: ChunkOrchestrationContext, run: _ChunkRun
) -> Deck | None:
    try:
        run.chunks = list(
            context.pdf_chunker.chunk_pdf(
                run.processing_path,
                run.pdf_output_path / ".temp_chunks",
            )
        )
        logger.info(f"Processing {len(run.chunks)} chunks independently...")
        context._prepare_resume(run)
        if not context._process_chunks(run):
            return None
        return context._combine_chunk_decks(run)
    finally:
        if not run.request.resume:
            context.pdf_chunker.cleanup_chunks(run.chunks)


def process_chunks(
    context: ChunkOrchestrationContext,
    run: _ChunkRun,
    chunk_delay_seconds: float,
) -> bool:
    for chunk_index, chunk_path in enumerate(run.chunks, 1):
        context._raise_if_cancelled()
        chunk_deck = context._get_or_process_chunk(
            run, chunk_index, chunk_path
        )
        if chunk_deck is None:
            return False
        context._log_chunk_result(chunk_index, len(run.chunks), chunk_deck)
        if chunk_index < len(run.chunks):
            logger.debug(
                f"Waiting {chunk_delay_seconds}s before next chunk..."
            )
            context._wait_or_cancel(chunk_delay_seconds)
    return True


def get_or_process_chunk(
    context: ChunkOrchestrationContext,
    run: _ChunkRun,
    task: _ChunkTask,
) -> Deck | None:
    if task.chunk_index in run.completed_indexes:
        logger.info(
            f"Skipping chunk {task.chunk_index}/{len(run.chunks)} - already done"
        )
        resumed_deck = run.chunk_decks[task.chunk_index]
        context._publish(
            ProgressStage.CHUNK,
            ProgressState.SKIPPED,
            "Using completed chunk",
            current=task.chunk_index,
            total=len(run.chunks),
            source=run.pdf_path,
            chunk_index=task.chunk_index,
            cards=len(resumed_deck.flashcards),
        )
        return resumed_deck

    context._publish(
        ProgressStage.CHUNK,
        ProgressState.STARTED,
        "Processing chunk",
        current=task.chunk_index,
        total=len(run.chunks),
        source=run.pdf_path,
        chunk_index=task.chunk_index,
    )
    logger.info(f"Processing chunk {task.chunk_index}/{len(run.chunks)}...")
    context._last_chunk_error_message = None
    chunk_deck = context._process_chunk(
        task.chunk_path,
        task.deck_name,
        task.pdf_output_path,
        task.request,
        task.chunk_index,
        task.total_chunks,
    )
    if chunk_deck is None:
        context._mark_chunk_failed(run, task.chunk_index)
        context._publish(
            ProgressStage.CHUNK,
            ProgressState.FAILED,
            "Chunk processing failed",
            current=task.chunk_index,
            total=len(run.chunks),
            source=run.pdf_path,
            chunk_index=task.chunk_index,
        )
        return None

    run.chunk_decks[task.chunk_index] = chunk_deck
    context._raise_if_cancelled()
    context._save_chunk_completion(run, task.chunk_index, chunk_deck)
    context._publish(
        ProgressStage.CHUNK,
        ProgressState.COMPLETED,
        "Chunk processing completed",
        current=task.chunk_index,
        total=len(run.chunks),
        source=run.pdf_path,
        chunk_index=task.chunk_index,
        cards=len(chunk_deck.flashcards),
    )
    return chunk_deck


def log_chunk_result(
    chunk_index: int, total_chunks: int, chunk_deck: Deck
) -> None:
    if chunk_deck.flashcards:
        logger.info(
            f"Chunk {chunk_index}/{total_chunks}: "
            f"{len(chunk_deck.flashcards)} flashcards"
        )
    else:
        logger.warning(
            f"Chunk {chunk_index}/{total_chunks}: no flashcards generated"
        )
