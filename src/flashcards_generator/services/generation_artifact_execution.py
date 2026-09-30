from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.domain_models.exceptions import NotebookCleanupError
from flashcards_generator.engines.cloze import ClozeConverter
from flashcards_generator.services.contracts import (
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.generation_models import _safe_filename
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)

logger = logging.getLogger("use_cases")


class ArtifactExecutionContext(Protocol):
    generator: FlashcardGeneratorPort
    converter: ClozeConverter
    _created_notebooks: list[str]

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

    def _raise_if_cancelled(self) -> None: ...

    def _handle_artifact_completion(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck | None: ...

    def _download_and_convert(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        pdf_stem: str = "",
        single_cloze: bool = False,
    ) -> Deck: ...

    def _download_flashcards(
        self, notebook_id: str, artifact_id: str, json_path: Path
    ) -> list[Flashcard]: ...

    def _cleanup_raw_file(self, json_path: Path) -> None: ...

    def _convert_flashcards(
        self,
        flashcards: list[Flashcard],
        deck_name: str,
        single_cloze: bool = False,
    ) -> list[Flashcard]: ...

    def _build_deck(
        self,
        notebook_id: str,
        deck_name: str,
        flashcards: list[Flashcard],
        single_cloze: bool = False,
    ) -> Deck: ...

    def _delete_completed_notebook(self, notebook_id: str) -> None: ...

    def _save_deck(
        self, deck: Deck, output_path: Path, pdf_stem: str
    ) -> None: ...

    def _cleanup_completed_resume_state(
        self,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> None: ...


def generate_flashcards(
    context: ArtifactExecutionContext,
    notebook_id: str,
    deck_name: str,
    pdf_output_path: Path,
    request: GenerateFlashcardsRequest,
    pdf_stem: str,
    default_instructions: str,
) -> Deck | None:
    instructions = request.instructions or default_instructions
    gen_config = GenerationConfig(
        difficulty=request.difficulty,
        quantity=request.quantity,
        instructions=instructions,
        timeout_seconds=request.timeout,
        wait_for_completion=request.wait_for_completion,
    )

    logger.info("Generating flashcards...")
    context._publish(
        ProgressStage.GENERATION,
        ProgressState.STARTED,
        "Generating flashcards",
    )
    context._raise_if_cancelled()
    artifact_id = context.generator.generate_flashcards(
        notebook_id, gen_config
    )
    context._raise_if_cancelled()

    if not artifact_id:
        logger.error("Failed to generate flashcards")
        context._publish(
            ProgressStage.GENERATION,
            ProgressState.FAILED,
            "Flashcard generation failed",
        )
        return None

    deck = context._handle_artifact_completion(
        notebook_id,
        artifact_id,
        pdf_output_path,
        deck_name,
        request,
        pdf_stem,
    )
    if deck is None:
        context._publish(
            ProgressStage.GENERATION,
            ProgressState.FAILED,
            "Flashcard generation timed out",
        )
        return None
    context._publish(
        ProgressStage.GENERATION,
        ProgressState.COMPLETED,
        "Flashcard generation completed",
        cards=len(deck.flashcards),
    )
    return deck


def save_completed_deck(
    context: ArtifactExecutionContext,
    deck: Deck | None,
    pdf_output_path: Path,
    pdf_stem: str,
    request: GenerateFlashcardsRequest,
) -> None:
    if deck is None or not request.wait_for_completion:
        return
    context._raise_if_cancelled()
    context._publish(
        ProgressStage.EXPORT,
        ProgressState.STARTED,
        "Exporting deck",
        cards=len(deck.flashcards),
    )
    try:
        context._save_deck(deck, pdf_output_path, pdf_stem)
    except OSError:
        context._publish(
            ProgressStage.EXPORT,
            ProgressState.FAILED,
            "Deck export failed",
            cards=len(deck.flashcards),
        )
        raise
    context._publish(
        ProgressStage.EXPORT,
        ProgressState.COMPLETED,
        "Deck exported",
        cards=len(deck.flashcards),
    )
    context._cleanup_completed_resume_state(pdf_output_path, pdf_stem, request)


def handle_artifact_completion(
    context: ArtifactExecutionContext,
    notebook_id: str,
    artifact_id: str,
    output_path: Path,
    deck_name: str,
    request: GenerateFlashcardsRequest,
    pdf_stem: str = "",
) -> Deck | None:
    logger.info("Waiting for generation...")

    if not request.wait_for_completion:
        logger.info(f"Background generation. ID: {artifact_id}")
        return Deck(
            name=deck_name,
            description=f"Deck {deck_name} (generating)",
            notebook_id=notebook_id,
        )

    context._raise_if_cancelled()
    completed = context.generator.wait_for_artifact(
        notebook_id, artifact_id, timeout=request.timeout
    )
    context._raise_if_cancelled()

    if completed:
        return context._download_and_convert(
            notebook_id,
            artifact_id,
            output_path,
            deck_name,
            pdf_stem,
            request.single_cloze,
        )

    logger.warning(f"Timeout. ID: {artifact_id}")
    return None


def download_and_convert(
    context: ArtifactExecutionContext,
    notebook_id: str,
    artifact_id: str,
    output_path: Path,
    deck_name: str,
    pdf_stem: str = "",
    single_cloze: bool = False,
) -> Deck:
    temp_name = pdf_stem if pdf_stem else deck_name
    json_path = output_path / _safe_filename(temp_name, "_raw.json")
    flashcards = context._download_flashcards(
        notebook_id, artifact_id, json_path
    )
    deck = context._build_deck(
        notebook_id, deck_name, flashcards, single_cloze
    )
    context._delete_completed_notebook(notebook_id)
    return deck


def download_flashcards(
    context: ArtifactExecutionContext,
    notebook_id: str,
    artifact_id: str,
    json_path: Path,
) -> list[Flashcard]:
    try:
        context._raise_if_cancelled()
        context.generator.download_flashcards(
            notebook_id, artifact_id, json_path
        )
        context._raise_if_cancelled()
        return context.generator.parse_flashcards(json_path)
    finally:
        context._cleanup_raw_file(json_path)


def cleanup_raw_file(json_path: Path) -> None:
    try:
        json_path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning(f"Failed to cleanup temp file: {error}")


def convert_flashcards(
    context: ArtifactExecutionContext,
    flashcards: list[Flashcard],
    deck_name: str,
    single_cloze: bool = False,
) -> list[Flashcard]:
    cloze_cards: list[Flashcard] = []
    tag = deck_name.lower().replace(" ", "_")
    for card in flashcards:
        cloze_card = context.converter.convert(card, single_cloze=single_cloze)
        if cloze_card:
            cloze_card.tags.append(tag)
            cloze_cards.append(cloze_card)
    return cloze_cards


def build_deck(
    context: ArtifactExecutionContext,
    notebook_id: str,
    deck_name: str,
    flashcards: list[Flashcard],
    single_cloze: bool = False,
) -> Deck:
    deck = Deck(
        name=deck_name,
        description=f"Deck de {deck_name}",
        flashcards=context._convert_flashcards(
            flashcards, deck_name, single_cloze
        ),
        notebook_id=notebook_id,
    )
    removed = deck.deduplicate(similarity_threshold=0.85)
    if removed > 0:
        logger.info(f"Removed {removed} duplicate flashcards")
    return deck


def delete_completed_notebook(
    context: ArtifactExecutionContext, notebook_id: str
) -> None:
    try:
        context.generator.delete_notebook(notebook_id)
        if notebook_id in context._created_notebooks:
            context._created_notebooks.remove(notebook_id)
        logger.info("Notebook deleted")
    except NotebookCleanupError:
        pass
