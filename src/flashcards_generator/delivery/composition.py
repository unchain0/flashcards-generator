"""Concrete dependency composition for UI-independent workflows."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from flashcards_generator.engines.cloze import ClozeConverter
from flashcards_generator.integrations.anki.connect import (
    AnkiConnectAdapter,
)
from flashcards_generator.integrations.chunk_state_repository import (
    FileSystemChunkStateRepository,
)
from flashcards_generator.integrations.csv_merger import CsvMerger
from flashcards_generator.integrations.deck_exporter import DeckExporter
from flashcards_generator.integrations.document_sources import (
    FileSystemDocumentSources,
)
from flashcards_generator.integrations.notebooklm.executable import (
    find_notebooklm,
)
from flashcards_generator.integrations.notebooklm.gateway import (
    NotebookLMAdapter,
)
from flashcards_generator.integrations.notebooklm.management import (
    AdapterFactory,
    NotebookLMManagement,
)
from flashcards_generator.integrations.pdf_utils import PDFChunker
from flashcards_generator.integrations.source_snapshot import (
    FileSystemSourceSnapshots,
)
from flashcards_generator.services.dto.merge_request import MergeCsvRequest
from flashcards_generator.services.dto.workflow import (
    AnkiExportOptions,
)
from flashcards_generator.services.generation_workflow import (
    UseCaseFactory,
    UseCaseGenerationWorkflow,
)
from flashcards_generator.services.use_cases import (
    GenerateFlashcardsUseCase,
)
from flashcards_generator.services.workflows import ApplicationWorkflows


def create_workflows(
    *,
    notebooklm_path: str | None = None,
    use_case_factory: UseCaseFactory | None = None,
    adapter_factory: AdapterFactory | None = None,
    notebooklm_profile: str | None = None,
    notebooklm_home: Path | None = None,
    merge_operation: Callable[[MergeCsvRequest], int] | None = None,
    cleanup_show_progress: bool = False,
) -> ApplicationWorkflows:
    """Compose the production workflow facade with replaceable factories."""
    executable = notebooklm_path or find_notebooklm()

    def default_adapter_factory(timeout: int) -> NotebookLMAdapter:
        return NotebookLMAdapter(
            executable,
            timeout=timeout,
            profile=notebooklm_profile,
            notebooklm_home=notebooklm_home,
        )

    resolved_adapter_factory = adapter_factory or default_adapter_factory

    def default_use_case_factory(timeout: int) -> GenerateFlashcardsUseCase:
        return GenerateFlashcardsUseCase(
            generator=resolved_adapter_factory(timeout),
            converter=ClozeConverter(),
            exporter=DeckExporter(),
            pdf_chunker=PDFChunker(),
            chunk_state_repository=FileSystemChunkStateRepository(),
            document_sources=FileSystemDocumentSources(),
            source_snapshots=FileSystemSourceSnapshots(),
        )

    generation = UseCaseGenerationWorkflow(
        use_case_factory or default_use_case_factory
    )
    management = NotebookLMManagement(
        executable,
        resolved_adapter_factory,
        notebooklm_profile=notebooklm_profile,
        notebooklm_home=notebooklm_home,
        cleanup_show_progress=cleanup_show_progress,
    )
    return ApplicationWorkflows(
        generation,
        management,
        merge_operation=(
            merge_operation
            if merge_operation is not None
            else CsvMerger.merge_detailed
        ),
        anki_exporter_factory=_create_anki_exporter,
    )


def _create_anki_exporter(options: AnkiExportOptions) -> AnkiConnectAdapter:
    return AnkiConnectAdapter(
        deck_name=options.deck_name,
        url=options.url,
        api_key=options.api_key,
    )
