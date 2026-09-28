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

logger = logging.getLogger("use_cases")


class GenerationRunContext(Protocol):
    _last_pdf_had_error: bool
    _last_run_had_errors: bool

    def _cleanup_orphaned_raw_files(self, output_path: Path) -> None: ...

    def _raise_if_cancelled(self) -> None: ...

    def _discover_sources(
        self, input_path: Path, request: GenerateFlashcardsRequest
    ) -> list[Path]: ...

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

    def _find_all_pdfs(
        self, input_path: Path, request: GenerateFlashcardsRequest
    ) -> list[Path]: ...

    def _process_sources(
        self,
        pdf_paths: list[Path],
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> list[Deck]: ...

    def _process_source(
        self,
        pdf_path: Path,
        current: int,
        total: int,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> Deck | None: ...

    def _source_progress_state(self, deck: Deck | None) -> ProgressState: ...

    def _process_pdf_entry(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> Deck | None: ...

    def _cleanup_notebooks(self) -> None: ...


def prepare_generation_paths(
    context: GenerationRunContext, request: GenerateFlashcardsRequest
) -> tuple[Path, Path]:
    input_path = request.input_dir.resolve(strict=True)
    output_path = request.output_dir
    output_path.mkdir(parents=True, exist_ok=True)
    resolved_output_path = output_path.resolve(strict=True)
    context._cleanup_orphaned_raw_files(resolved_output_path)
    return input_path, resolved_output_path


def generate_decks(
    context: GenerationRunContext,
    request: GenerateFlashcardsRequest,
    input_path: Path,
    output_path: Path,
) -> list[Deck]:
    context._raise_if_cancelled()
    all_pdfs = context._discover_sources(input_path, request)
    if not all_pdfs:
        logger.warning(f"No PDFs found in {input_path}")
        return []

    logger.info(f"{len(all_pdfs)} PDF(s) found")
    return context._process_sources(
        sorted(all_pdfs), input_path, output_path, request
    )


def discover_sources(
    context: GenerationRunContext,
    input_path: Path,
    request: GenerateFlashcardsRequest,
) -> list[Path]:
    context._publish(
        ProgressStage.DISCOVERY,
        ProgressState.STARTED,
        "Discovering sources",
        current=0,
    )
    all_pdfs = context._find_all_pdfs(input_path, request)
    context._publish(
        ProgressStage.DISCOVERY,
        ProgressState.COMPLETED,
        "Source discovery completed",
        current=len(all_pdfs),
        total=len(all_pdfs),
    )
    return all_pdfs


def process_sources(
    context: GenerationRunContext,
    pdf_paths: list[Path],
    input_path: Path,
    output_path: Path,
    request: GenerateFlashcardsRequest,
) -> list[Deck]:
    decks: list[Deck] = []
    for current, pdf_path in enumerate(pdf_paths, 1):
        deck = context._process_source(
            pdf_path,
            current,
            len(pdf_paths),
            input_path,
            output_path,
            request,
        )
        if deck:
            decks.append(deck)
    return decks


def process_source(
    context: GenerationRunContext,
    pdf_path: Path,
    current: int,
    total: int,
    input_path: Path,
    output_path: Path,
    request: GenerateFlashcardsRequest,
) -> Deck | None:
    context._raise_if_cancelled()
    context._last_pdf_had_error = False
    context._publish(
        ProgressStage.SOURCE,
        ProgressState.STARTED,
        "Processing source",
        current=current,
        total=total,
        source=pdf_path,
    )
    deck = context._process_pdf_entry(
        pdf_path, input_path, output_path, request
    )
    context._last_run_had_errors |= context._last_pdf_had_error
    context._publish(
        ProgressStage.SOURCE,
        context._source_progress_state(deck),
        "Source processing finished",
        current=current,
        total=total,
        source=pdf_path,
        cards=len(deck.flashcards) if deck else None,
    )
    return deck


def source_progress_state(
    context: GenerationRunContext, deck: Deck | None
) -> ProgressState:
    if context._last_pdf_had_error:
        return ProgressState.FAILED
    if deck:
        return ProgressState.COMPLETED
    return ProgressState.SKIPPED


def cleanup_generation_resources(context: GenerationRunContext) -> None:
    context._publish(
        ProgressStage.CLEANUP,
        ProgressState.STARTED,
        "Cleaning up generation resources",
    )
    context._cleanup_notebooks()
    context._publish(
        ProgressStage.CLEANUP,
        ProgressState.COMPLETED,
        "Generation resources cleaned up",
    )
