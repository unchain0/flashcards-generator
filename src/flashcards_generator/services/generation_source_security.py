from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)

logger = logging.getLogger("use_cases")


class SourceSecurityContext(Protocol):
    _last_pdf_had_error: bool

    def _is_safe_file_path(
        self, file_path: Path, input_path: Path
    ) -> bool: ...

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path: ...

    def _snapshot_source(
        self, pdf_path: Path, pdf_output_path: Path
    ) -> Path | None: ...

    def _process_pdf_with_snapshot_cleanup(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_snapshot: Path,
    ) -> Deck | None: ...

    def _process_pdf_with_lock(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_snapshot: Path,
    ) -> Deck | None: ...

    def _cleanup_source_snapshot(self, source_snapshot: Path) -> None: ...

    def _get_resume_lock(
        self,
        pdf_path: Path,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_snapshot: Path,
    ) -> AbstractContextManager[bool] | None: ...

    def _process_pdf(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
        source_path: Path,
    ) -> Deck | None: ...

    def _save_completed_deck(
        self,
        deck: Deck | None,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> None: ...


def process_pdf_entry(
    context: SourceSecurityContext,
    pdf_path: Path,
    input_path: Path,
    output_path: Path,
    request: GenerateFlashcardsRequest,
) -> Deck | None:
    if not context._is_safe_file_path(pdf_path, input_path):
        return None

    pdf_output_path = context._get_output_subdir(
        pdf_path, input_path, output_path
    )
    source_snapshot = context._snapshot_source(pdf_path, pdf_output_path)
    if source_snapshot is None:
        context._last_pdf_had_error = True
        return None

    return context._process_pdf_with_snapshot_cleanup(
        pdf_path,
        input_path,
        output_path,
        pdf_output_path,
        request,
        source_snapshot,
    )


def process_pdf_with_snapshot_cleanup(
    context: SourceSecurityContext,
    pdf_path: Path,
    input_path: Path,
    output_path: Path,
    pdf_output_path: Path,
    request: GenerateFlashcardsRequest,
    source_snapshot: Path,
) -> Deck | None:
    try:
        result = context._process_pdf_with_lock(
            pdf_path,
            input_path,
            output_path,
            pdf_output_path,
            request,
            source_snapshot,
        )
    except BaseException as error:
        try:
            context._cleanup_source_snapshot(source_snapshot)
        except OSError as cleanup_error:
            error.add_note(
                "Cleaning up the source snapshot also failed "
                f"({type(cleanup_error).__name__})"
            )
        raise
    context._cleanup_source_snapshot(source_snapshot)
    return result


def process_pdf_with_lock(
    context: SourceSecurityContext,
    pdf_path: Path,
    input_path: Path,
    output_path: Path,
    pdf_output_path: Path,
    request: GenerateFlashcardsRequest,
    source_snapshot: Path,
) -> Deck | None:
    resume_lock = context._get_resume_lock(
        pdf_path, pdf_output_path, request, source_snapshot
    )
    if resume_lock is None:
        return None

    with resume_lock as owns_resume:
        if not owns_resume:
            logger.warning(
                f"Skipping {pdf_path.name}: resume is already running"
            )
            return None

        deck = context._process_pdf(
            pdf_path, input_path, output_path, request, source_snapshot
        )
        context._save_completed_deck(
            deck, pdf_output_path, pdf_path.stem, request
        )
        return deck
