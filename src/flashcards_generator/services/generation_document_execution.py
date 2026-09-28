from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, assert_never

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.domain_models.exceptions import GenerationError
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.generation_models import (
    BORDER_LENGTH,
    PDF_CHUNKING_THRESHOLD,
    SOURCE_WAIT_TIMEOUT,
    _safe_filename,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
)
from flashcards_generator.services.ports.pdf_chunker import PDFChunkerPort

logger = logging.getLogger("use_cases")


class DocumentExecutionContext(Protocol):
    generator: FlashcardGeneratorPort
    _last_pdf_had_error: bool

    @property
    def pdf_chunker(self) -> PDFChunkerPort: ...

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str: ...

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path: ...

    def _output_deck_exists(
        self, pdf_output_path: Path, pdf_stem: str
    ) -> bool: ...

    def _log_pdf_header(
        self, pdf_path: Path, input_path: Path, deck_name: str
    ) -> None: ...

    def _process_pdf_content(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None: ...

    def _log_pdf_processing_error(
        self,
        error: (GenerationError | OSError | ValueError | RuntimeError),
    ) -> None: ...

    def _should_chunk_pdf(
        self, pdf_path: Path, processing_path: Path
    ) -> bool: ...

    def _process_large_pdf(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_path: Path | None = None,
    ) -> Deck | None: ...

    def _process_regular_pdf(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None: ...

    def _create_notebook(self, deck_name: str) -> str: ...

    def _add_pdf_source(
        self, notebook_id: str, pdf_path: Path
    ) -> str | None: ...

    def _raise_if_cancelled(self) -> None: ...

    def _generate_flashcards(
        self,
        notebook_id: str,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck | None: ...


def process_pdf(
    context: DocumentExecutionContext,
    pdf_path: Path,
    input_path: Path,
    output_path: Path,
    request: GenerateFlashcardsRequest,
    source_path: Path | None = None,
) -> Deck | None:
    deck_name = context._get_deck_name(pdf_path, input_path)
    pdf_output_path = context._get_output_subdir(
        pdf_path, input_path, output_path
    )
    if context._output_deck_exists(pdf_output_path, pdf_path.stem):
        logger.info(f"Skipping {pdf_path.name} - CSV already exists")
        return None

    context._log_pdf_header(pdf_path, input_path, deck_name)
    processing_path = pdf_path if source_path is None else source_path
    try:
        return context._process_pdf_content(
            pdf_path,
            deck_name,
            pdf_output_path,
            request,
            processing_path,
        )
    except (
        GenerationError,
        OSError,
        ValueError,
        RuntimeError,
    ) as error:
        context._log_pdf_processing_error(error)
        return None


def log_pdf_processing_error(
    context: DocumentExecutionContext,
    error: (GenerationError | OSError | ValueError | RuntimeError),
) -> None:
    context._last_pdf_had_error = True
    match error:
        case GenerationError():
            logger.error(f"Generation error: {error}")
        case OSError() | ValueError() | RuntimeError():
            logger.error(f"Processing error: {error}")
        case unreachable:
            assert_never(unreachable)


def output_deck_exists(pdf_output_path: Path, pdf_stem: str) -> bool:
    return (pdf_output_path / _safe_filename(pdf_stem, ".csv")).exists()


def log_pdf_header(pdf_path: Path, input_path: Path, deck_name: str) -> None:
    logger.info("=" * BORDER_LENGTH)
    logger.info(f"PDF: {pdf_path.relative_to(input_path)}")
    logger.info(f"Deck: {deck_name}")
    logger.info("=" * BORDER_LENGTH)


def process_pdf_content(
    context: DocumentExecutionContext,
    pdf_path: Path,
    deck_name: str,
    pdf_output_path: Path,
    request: GenerateFlashcardsRequest,
    processing_path: Path,
) -> Deck | None:
    if context._should_chunk_pdf(pdf_path, processing_path):
        logger.info(
            f"Large PDF detected (>{PDF_CHUNKING_THRESHOLD} pages), "
            "using chunking..."
        )
        deck = context._process_large_pdf(
            pdf_path,
            deck_name,
            pdf_output_path,
            request,
            processing_path,
        )
    else:
        deck = context._process_regular_pdf(
            pdf_path,
            deck_name,
            pdf_output_path,
            request,
            processing_path,
        )
    if deck is None:
        context._last_pdf_had_error = True
    return deck


def should_chunk_pdf(
    context: DocumentExecutionContext,
    pdf_path: Path,
    processing_path: Path,
) -> bool:
    return (
        pdf_path.suffix.lower() == ".pdf"
        and context.pdf_chunker.needs_chunking(
            processing_path, threshold=PDF_CHUNKING_THRESHOLD
        )
    )


def process_regular_pdf(
    context: DocumentExecutionContext,
    pdf_path: Path,
    deck_name: str,
    pdf_output_path: Path,
    request: GenerateFlashcardsRequest,
    processing_path: Path,
) -> Deck | None:
    notebook_id = context._create_notebook(deck_name)
    source_id = context._add_pdf_source(notebook_id, processing_path)
    if not source_id:
        context._last_pdf_had_error = True
        return None
    logger.info("Processing source...")
    context._raise_if_cancelled()
    source_ready = context.generator.wait_for_source(
        notebook_id, source_id, timeout=SOURCE_WAIT_TIMEOUT
    )
    context._raise_if_cancelled()
    if not source_ready:
        logger.warning(f"Source processing timed out: {pdf_path.name}")
        return None
    deck = context._generate_flashcards(
        notebook_id, deck_name, pdf_output_path, request, pdf_path.stem
    )
    if deck is None:
        context._last_pdf_had_error = True
    return deck
