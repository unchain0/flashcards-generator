from __future__ import annotations

from flashcards_generator.engines.cloze import ClozeConverter
from flashcards_generator.integrations.document_sources import (
    FileSystemDocumentSources,
)
from flashcards_generator.integrations.pdf_utils import PDFChunker
from flashcards_generator.integrations.source_snapshot import (
    FileSystemSourceSnapshots,
)
from flashcards_generator.services.exporter import DeckExporter
from flashcards_generator.services.ports import (
    ChunkStatePort,
    DocumentSourcesPort,
    SourceSnapshotsPort,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
)
from flashcards_generator.services.use_cases import GenerateFlashcardsUseCase


class _TestGenerateFlashcardsUseCase(GenerateFlashcardsUseCase):
    pdf_chunker: PDFChunker


def make_use_case(
    generator: FlashcardGeneratorPort,
    *,
    converter: ClozeConverter | None = None,
    exporter: DeckExporter | None = None,
    pdf_chunker: PDFChunker | None = None,
    chunk_state_repository: ChunkStatePort | None = None,
    document_sources: DocumentSourcesPort | None = None,
    source_snapshots: SourceSnapshotsPort | None = None,
) -> _TestGenerateFlashcardsUseCase:
    return _TestGenerateFlashcardsUseCase(
        generator=generator,
        converter=converter if converter is not None else ClozeConverter(),
        exporter=exporter if exporter is not None else DeckExporter(),
        pdf_chunker=pdf_chunker if pdf_chunker is not None else PDFChunker(),
        chunk_state_repository=chunk_state_repository,
        document_sources=(
            document_sources
            if document_sources is not None
            else FileSystemDocumentSources()
        ),
        source_snapshots=(
            source_snapshots
            if source_snapshots is not None
            else FileSystemSourceSnapshots()
        ),
    )
