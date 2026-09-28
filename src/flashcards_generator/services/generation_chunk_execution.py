from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.domain_models.exceptions import (
    GenerationError,
    NotebookCleanupError,
    SourceProcessingError,
)
from flashcards_generator.services.contracts import (
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.generation_models import (
    SOURCE_WAIT_TIMEOUT,
    _ChunkTask,
    _safe_filename,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)

logger = logging.getLogger("use_cases")


class ChunkExecutionContext(Protocol):
    generator: FlashcardGeneratorPort
    _created_notebooks: list[str]

    def _create_notebook(self, deck_name: str) -> str: ...

    def _add_pdf_source(
        self, notebook_id: str, pdf_path: Path
    ) -> str | None: ...

    def _raise_if_cancelled(self) -> None: ...

    def _run_chunk_generation(
        self, notebook_id: str, task: _ChunkTask
    ) -> Deck | None: ...

    def _generate_chunk_artifact(
        self, notebook_id: str, task: _ChunkTask
    ) -> str | None: ...

    def _download_chunk_deck(
        self, notebook_id: str, artifact_id: str, task: _ChunkTask
    ) -> Deck: ...

    def _cleanup_chunk_notebook(
        self, notebook_id: str | None, chunk_index: int, total_chunks: int
    ) -> None: ...

    def _cleanup_raw_file(self, json_path: Path) -> None: ...

    def _convert_flashcards(
        self, flashcards: list[Flashcard], deck_name: str
    ) -> list[Flashcard]: ...

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


def process_chunk_internal(
    context: ChunkExecutionContext, task: _ChunkTask
) -> Deck | None:
    chunk_notebook_id: str | None = None
    try:
        chunk_deck_name = f"{task.deck_name}_chunk{task.chunk_index}"
        chunk_notebook_id = context._create_notebook(chunk_deck_name)
        return context._run_chunk_generation(chunk_notebook_id, task)
    except (
        GenerationError,
        SourceProcessingError,
        OSError,
        RuntimeError,
    ) as error:
        logger.error(
            f"Chunk {task.chunk_index}/{task.total_chunks}: error - {error}"
        )
        raise
    finally:
        context._cleanup_chunk_notebook(
            chunk_notebook_id, task.chunk_index, task.total_chunks
        )


def run_chunk_generation(
    context: ChunkExecutionContext,
    notebook_id: str,
    task: _ChunkTask,
) -> Deck | None:
    """Add a chunk source, generate its artifact, and convert the result."""
    source_id = context._add_pdf_source(notebook_id, task.chunk_path)
    if not source_id:
        logger.error(f"Failed to add chunk {task.chunk_index} as source")
        return None

    logger.info(
        f"Chunk {task.chunk_index}/{task.total_chunks}: "
        "source added, waiting..."
    )
    context._raise_if_cancelled()
    source_ready = context.generator.wait_for_source(
        notebook_id, source_id, timeout=SOURCE_WAIT_TIMEOUT
    )
    context._raise_if_cancelled()
    if not source_ready:
        logger.warning(
            f"Chunk {task.chunk_index}/{task.total_chunks}: "
            "source processing timed out"
        )
        return None
    artifact_id = context._generate_chunk_artifact(notebook_id, task)
    if not artifact_id:
        logger.error(
            f"Chunk {task.chunk_index}/{task.total_chunks}: failed to generate"
        )
        context._publish(
            ProgressStage.GENERATION,
            ProgressState.FAILED,
            "Chunk generation failed",
            chunk_index=task.chunk_index,
        )
        return None

    context._raise_if_cancelled()
    completed = context.generator.wait_for_artifact(
        notebook_id, artifact_id, timeout=task.request.timeout
    )
    context._raise_if_cancelled()
    if not completed:
        logger.warning(
            f"Chunk {task.chunk_index}/{task.total_chunks}: timeout"
        )
        context._publish(
            ProgressStage.GENERATION,
            ProgressState.FAILED,
            "Chunk generation timed out",
            chunk_index=task.chunk_index,
        )
        return None
    deck = context._download_chunk_deck(notebook_id, artifact_id, task)
    context._publish(
        ProgressStage.GENERATION,
        ProgressState.COMPLETED,
        "Chunk generation completed",
        chunk_index=task.chunk_index,
        cards=len(deck.flashcards),
    )
    return deck


def generate_chunk_artifact(
    context: ChunkExecutionContext,
    notebook_id: str,
    task: _ChunkTask,
    default_instructions: str,
) -> str | None:
    """Generate an artifact using instructions scoped to one chunk."""
    instructions = task.request.instructions or default_instructions
    chunk_instructions = (
        f"{instructions}\n\n"
        f"CONTEXT: This is part {task.chunk_index} of "
        f"{task.total_chunks} of the document."
    )
    gen_config = GenerationConfig(
        difficulty=task.request.difficulty,
        quantity=task.request.quantity,
        instructions=chunk_instructions,
        timeout_seconds=task.request.timeout,
        wait_for_completion=task.request.wait_for_completion,
    )
    logger.info(
        f"Chunk {task.chunk_index}/{task.total_chunks}: "
        "generating flashcards..."
    )
    context._publish(
        ProgressStage.GENERATION,
        ProgressState.STARTED,
        "Generating chunk flashcards",
        chunk_index=task.chunk_index,
    )
    context._raise_if_cancelled()
    artifact_id = context.generator.generate_flashcards(
        notebook_id, gen_config
    )
    context._raise_if_cancelled()
    return artifact_id


def download_chunk_deck(
    context: ChunkExecutionContext,
    notebook_id: str,
    artifact_id: str,
    task: _ChunkTask,
) -> Deck:
    """Download one completed chunk and convert its cards."""
    json_path = task.pdf_output_path / _safe_filename(
        f"chunk{task.chunk_index}", "_raw.json"
    )
    try:
        context.generator.download_flashcards(
            notebook_id, artifact_id, json_path
        )
        flashcards = context.generator.parse_flashcards(json_path)
    finally:
        context._cleanup_raw_file(json_path)

    cloze_cards = context._convert_flashcards(flashcards, task.deck_name)
    return Deck(
        name=f"{task.deck_name}_chunk{task.chunk_index}",
        description=f"Chunk {task.chunk_index} of {task.total_chunks}",
        flashcards=cloze_cards,
        notebook_id=notebook_id,
    )


def cleanup_chunk_notebook(
    context: ChunkExecutionContext,
    notebook_id: str | None,
    chunk_index: int,
    total_chunks: int,
) -> None:
    """Delete a completed chunk notebook when it is still tracked."""
    if not notebook_id or notebook_id not in context._created_notebooks:
        return
    try:
        context.generator.delete_notebook(notebook_id)
        context._created_notebooks.remove(notebook_id)
        logger.debug(
            f"Chunk {chunk_index}/{total_chunks}: notebook cleaned up"
        )
    except NotebookCleanupError as error:
        logger.warning(f"Failed to cleanup chunk notebook: {error}")
