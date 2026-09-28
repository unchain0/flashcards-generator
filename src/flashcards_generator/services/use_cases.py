"""Generate flashcards use case with dependency injection."""

from __future__ import annotations

import logging
import time
from contextlib import AbstractContextManager, nullcontext, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from flashcards_generator.domain_models.entities import (
    Deck,
    Flashcard,
)
from flashcards_generator.domain_models.exceptions import (
    GenerationError,
    NotebookCleanupError,
    SourceProcessingError,
)
from flashcards_generator.engines.cloze import ClozeConverter
from flashcards_generator.engines.quality import QualityFilter
from flashcards_generator.services import (
    generation_artifact_execution,
    generation_chunk_execution,
    generation_chunk_orchestration,
    generation_chunk_retry,
    generation_document_execution,
    generation_models,
    generation_quality,
    generation_run,
    generation_source_security,
)
from flashcards_generator.services.chunk_resume import (
    get_chunk_result_path,
    mark_chunk_failed,
    prepare_resume,
    save_chunk_completion,
)
from flashcards_generator.services.contracts import (
    CancellationToken,
    NullProgressReporter,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.exporter import DeckExporter
from flashcards_generator.services.ports.document_sources import (
    DocumentSelection,
    DocumentSourcesPort,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from flashcards_generator.services.ports.pdf_chunker import PDFChunkerPort
from flashcards_generator.services.ports.source_snapshots import (
    SourceSnapshotsPort,
)

if TYPE_CHECKING:
    from flashcards_generator.services.ports import ChunkStatePort

# Explicit runtime usage to prevent type-checking-only false positives
_ = (Path, GenerateFlashcardsRequest, GenerationConfig)

logger = logging.getLogger("use_cases")


BORDER_LENGTH = generation_models.BORDER_LENGTH
CHUNK_DELAY_SECONDS = generation_models.CHUNK_DELAY_SECONDS
CHUNK_RETRY_BACKOFF_MULTIPLIER = (
    generation_models.CHUNK_RETRY_BACKOFF_MULTIPLIER
)
CHUNK_RETRY_INITIAL_DELAY = generation_models.CHUNK_RETRY_INITIAL_DELAY
CHUNK_RETRY_MAX_ATTEMPTS = generation_models.CHUNK_RETRY_MAX_ATTEMPTS
CHUNK_RETRY_MAX_DELAY = generation_models.CHUNK_RETRY_MAX_DELAY
MAX_FILENAME_LEN = generation_models.MAX_FILENAME_LEN
MIN_CARDS_QUALITY_LENGTH = generation_models.MIN_CARDS_QUALITY_LENGTH
PDF_CHUNKING_THRESHOLD = generation_models.PDF_CHUNKING_THRESHOLD
SOURCE_WAIT_TIMEOUT = generation_models.SOURCE_WAIT_TIMEOUT
_ChunkAttemptResult = generation_models._ChunkAttemptResult
_ChunkRun = generation_models._ChunkRun
_ChunkTask = generation_models._ChunkTask
_safe_filename = generation_models._safe_filename


class GenerateFlashcardsUseCase:
    """Use case for generating flashcards from PDF files.

    Dependencies:
        - generator: FlashcardGeneratorPort implementation
        - converter: ClozeConverter instance
        - exporter: DeckExporter instance
    """

    DEFAULT_INSTRUCTIONS = (
        "Crie flashcards para recuperação ativa e repetição espaçada usando "
        "somente informações explicitamente sustentadas pela fonte. "
        "SELEÇÃO: priorize fundamentos, definições, relações causais, condições, "
        "distinções e etapas essenciais. Ignore títulos, repetições, detalhes "
        "decorativos, opiniões e trechos incompletos. Não tente cobrir todo o texto. "
        "FORMATO OBRIGATÓRIO: use apenas Cloze Deletion. A frente deve ser uma "
        "frase declarativa natural com exatamente uma lacuna {{c1::resposta}}. "
        "Exemplo: 'A {{c1::mitocôndria}} produz a maior parte do ATP celular.' "
        "QUALIDADE DE CADA CARD: "
        "1. Teste uma única ideia independente. Divida frases com mais de um fato. "
        "2. A lacuna deve ocultar a menor resposta significativa possível, "
        "preferencialmente de uma a cinco palavras; nunca oculte palavras triviais. "
        "3. Depois de ocultar a resposta, a frase deve continuar auto-contida e "
        "permitir uma única resposta esperada. Inclua o qualificador mínimo que "
        "elimine ambiguidades e interferência com conceitos semelhantes. "
        "4. Não deixe na frente sinônimos, traduções, paráfrases ou pistas "
        "gramaticais que revelem a resposta. Use {{c1::termo::dica}} apenas quando "
        "uma dica curta for indispensável para tornar a pergunta inequívoca. "
        "5. Mantenha a frente curta, idealmente até 25 palavras, sem perder o "
        "contexto necessário. O verso deve trazer apenas uma explicação breve "
        "do porquê, mecanismo ou contexto já presente na fonte. "
        "6. Evite listas. Converta cada item em uma relação significativa própria. "
        "Se a ordem for essencial, teste uma etapa por card e mantenha visível "
        "apenas o contexto necessário para localizar essa etapa. Nunca agrupe uma "
        "lista inteira em clozes c1, c2, c3. "
        "7. Para conceitos parecidos, formule pistas que destaquem a diferença "
        "diagnóstica em vez de criar cartões quase idênticos e ambíguos. "
        "8. Para código, oculte apenas o identificador, operador ou expressão-chave; "
        "nunca blocos inteiros. Para matemática, preserve a notação em LaTeX $...$. "
        "9. Não invente exemplos, relações, definições ou conclusões. Use exemplos "
        "somente quando estiverem na fonte e forem necessários para compreensão. "
        "10. Não gere duplicatas nem cartões que possam ser respondidos apenas por "
        "senso comum, estrutura da frase ou reconhecimento superficial. "
        "CONTEXTO DO DOCUMENTO: trabalhe somente com o conteúdo completo desta "
        "seção. Se um conceito depender de outra parte ou não estiver claro, "
        "ignore-o. "
        "Antes de finalizar, descarte qualquer card que não seja fiel à fonte, "
        "atômico, inequívoco, auto-contido e útil para recuperação ativa. "
        "SAÍDA: Frente (cloze); Verso (explicação breve)."
    )

    def __init__(
        self,
        generator: FlashcardGeneratorPort,
        converter: ClozeConverter,
        exporter: DeckExporter,
        pdf_chunker: PDFChunkerPort,
        chunk_state_repository: ChunkStatePort | None,
        document_sources: DocumentSourcesPort,
        source_snapshots: SourceSnapshotsPort,
    ):
        self.generator = generator
        self.converter = converter
        self.exporter = exporter
        self.pdf_chunker = pdf_chunker
        self._chunk_state_repository = chunk_state_repository
        self._document_sources = document_sources
        self._source_snapshots = source_snapshots
        self._created_notebooks: list[str] = []
        self._last_chunk_error_message: str | None = None
        self._last_pdf_had_error = False
        self._last_run_had_errors = False
        self._token: CancellationToken | None = None
        self._reporter: ProgressReporter = NullProgressReporter()

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
    ) -> None:
        """Publish one framework-neutral workflow update."""
        self._reporter.publish(
            ProgressEvent(
                stage=stage,
                state=state,
                message=message,
                current=current,
                total=total,
                source=source,
                chunk_index=chunk_index,
                cards=cards,
            )
        )

    def _raise_if_cancelled(self) -> None:
        """Raise when cancellation has been requested for this run."""
        if self._token is not None:
            self._token.raise_if_cancelled()

    def _wait_or_cancel(self, timeout: float) -> None:
        """Wait without making a cancellable run sleep uninterruptibly."""
        if self._token is None:
            time.sleep(timeout)
            return
        self._token.wait_or_cancel(timeout)

    @property
    def last_run_had_errors(self) -> bool:
        """Whether the latest execution failed to process a source."""
        return self._last_run_had_errors

    def execute(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter | None = None,
        token: CancellationToken | None = None,
    ) -> list[Deck]:
        """Execute generation with optional progress and cancellation."""
        self._reporter = reporter or NullProgressReporter()
        self._token = token
        input_path, output_path = self._prepare_generation_paths(request)
        self._last_run_had_errors = False

        with self.generator.cancellation_scope(self._token):
            try:
                return self._generate_decks(request, input_path, output_path)
            finally:
                self._cleanup_generation_resources()

    def _prepare_generation_paths(
        self, request: GenerateFlashcardsRequest
    ) -> tuple[Path, Path]:
        """Resolve generation roots and remove stale raw artifacts."""
        return generation_run.prepare_generation_paths(self, request)

    def _generate_decks(
        self,
        request: GenerateFlashcardsRequest,
        input_path: Path,
        output_path: Path,
    ) -> list[Deck]:
        """Discover sources and process each one in deterministic order."""
        return generation_run.generate_decks(
            self, request, input_path, output_path
        )

    def _discover_sources(
        self, input_path: Path, request: GenerateFlashcardsRequest
    ) -> list[Path]:
        """Find sources while publishing discovery boundaries."""
        return generation_run.discover_sources(self, input_path, request)

    def _process_sources(
        self,
        pdf_paths: list[Path],
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> list[Deck]:
        """Process discovered sources while retaining successful decks."""
        return generation_run.process_sources(
            self, pdf_paths, input_path, output_path, request
        )

    def _process_source(
        self,
        pdf_path: Path,
        current: int,
        total: int,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> Deck | None:
        """Process one source and publish its structured outcome."""
        return generation_run.process_source(
            self,
            pdf_path,
            current,
            total,
            input_path,
            output_path,
            request,
        )

    def _source_progress_state(self, deck: Deck | None) -> ProgressState:
        """Map the latest source outcome to its progress state."""
        return generation_run.source_progress_state(self, deck)

    def _cleanup_generation_resources(self) -> None:
        """Clean tracked notebooks while publishing cleanup boundaries."""
        generation_run.cleanup_generation_resources(self)

    def _process_pdf_entry(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> Deck | None:
        """Process one discovered source while cleaning its snapshot."""
        return generation_source_security.process_pdf_entry(
            self, pdf_path, input_path, output_path, request
        )

    def _process_pdf_with_snapshot_cleanup(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_snapshot: Path,
    ) -> Deck | None:
        return generation_source_security.process_pdf_with_snapshot_cleanup(
            self,
            pdf_path,
            input_path,
            output_path,
            pdf_output_path,
            request,
            source_snapshot,
        )

    def _process_pdf_with_lock(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_snapshot: Path,
    ) -> Deck | None:
        """Run one PDF while holding its optional resume lock."""
        return generation_source_security.process_pdf_with_lock(
            self,
            pdf_path,
            input_path,
            output_path,
            pdf_output_path,
            request,
            source_snapshot,
        )

    def _save_completed_deck(
        self,
        deck: Deck | None,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> None:
        """Persist a completed, wait-mode deck and clear its resume state."""
        generation_artifact_execution.save_completed_deck(
            self, deck, pdf_output_path, pdf_stem, request
        )

    def _cleanup_completed_resume_state(
        self,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> None:
        if request.resume and self._chunk_state_repository:
            self._cleanup_resume_state(pdf_output_path, pdf_stem)

    def _get_resume_lock(
        self,
        pdf_path: Path,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_snapshot: Path,
    ) -> AbstractContextManager[bool] | None:
        """Return a lock only for resumable, chunked PDFs."""
        if not request.resume:
            return nullcontext(True)
        try:
            if not self._should_chunk_pdf(pdf_path, source_snapshot):
                return nullcontext(True)
        except (OSError, ValueError, RuntimeError) as error:
            self._last_pdf_had_error = True
            logger.error(
                f"Unable to inspect {pdf_path.name} for chunking: {error}"
            )
            return None
        return self._resume_lock(pdf_output_path, pdf_path.stem, request)

    def _cleanup_source_snapshot(self, source_snapshot: Path) -> None:
        self._source_snapshots.cleanup(source_snapshot)

    def _get_resume_dir(self, pdf_output_path: Path, pdf_stem: str) -> Path:
        """Return the directory used to persist resume state."""
        return (
            pdf_output_path / ".flashcards_resume" / _safe_filename(pdf_stem)
        )

    def _get_state_file_path(
        self, pdf_output_path: Path, pdf_stem: str
    ) -> Path:
        """Return the manifest path for a chunked PDF."""
        return self._get_resume_dir(pdf_output_path, pdf_stem) / "state.json"

    def _get_chunk_result_path(
        self, resume_dir: Path, chunk_index: int
    ) -> Path:
        """Return the persisted result path for a chunk."""
        return get_chunk_result_path(resume_dir, chunk_index)

    def _compute_source_signature(self, pdf_path: Path) -> str:
        """Compute a content signature for resume validation."""
        return self._source_snapshots.compute_signature(pdf_path)

    def _snapshot_source(
        self, pdf_path: Path, pdf_output_path: Path
    ) -> Path | None:
        """Copy a validated source through a no-follow descriptor."""
        try:
            return self._source_snapshots.create(pdf_path, pdf_output_path)
        except OSError as error:
            logger.warning(
                f"Skipping changed or unsafe input {pdf_path}: {error}"
            )
            return None

    def _resume_lock(
        self,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> AbstractContextManager[bool]:
        """Return exclusive resume ownership when the filesystem port supports it."""
        repository = self._chunk_state_repository
        if request.resume and repository is not None:
            return repository.resume_lock(
                self._get_resume_dir(pdf_output_path, pdf_stem)
            )
        return nullcontext(True)

    def _cleanup_resume_state(
        self, pdf_output_path: Path, pdf_stem: str
    ) -> None:
        """Remove persisted resume artifacts after successful completion."""
        if not self._chunk_state_repository:
            return

        resume_dir = self._get_resume_dir(pdf_output_path, pdf_stem)
        state_path = self._get_state_file_path(pdf_output_path, pdf_stem)
        temp_chunks = list((pdf_output_path / ".temp_chunks").glob("*.pdf"))

        self._chunk_state_repository.delete_manifest(state_path)
        self._chunk_state_repository.delete_chunk_results(resume_dir)
        self.pdf_chunker.cleanup_chunks(temp_chunks)

        with suppress(OSError):
            (pdf_output_path / ".temp_chunks").rmdir()

        logger.info("Resume state cleaned up")

    def _find_all_pdfs(
        self, input_path: Path, request: GenerateFlashcardsRequest
    ) -> list[Path]:
        return self._document_sources.find_all_sources(
            input_path,
            DocumentSelection(
                explicit_files=tuple(request.explicit_files),
                include_pattern=request.include_pattern,
                exclude_pattern=request.exclude_pattern,
            ),
        )

    def _is_safe_file_path(self, file_path: Path, input_path: Path) -> bool:
        return self._document_sources.is_safe_source(file_path, input_path)

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str:
        return self._document_sources.get_deck_name(pdf_path, input_path)

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        return self._document_sources.get_output_subdir(
            pdf_path, input_path, output_path
        )

    def _cleanup_notebooks(self) -> None:
        """Clean up created notebooks."""
        if not self._created_notebooks:
            return

        logger.info(
            f"Cleaning up {len(self._created_notebooks)} notebook(s)..."
        )
        for notebook_id in self._created_notebooks:
            try:
                self.generator.delete_notebook(notebook_id)
                logger.info(f"Deleted: {notebook_id[:8]}...")
            except NotebookCleanupError:
                pass
        self._created_notebooks.clear()

    def _cleanup_orphaned_raw_files(self, output_path: Path) -> None:
        for raw_file in output_path.rglob("*_raw.json"):
            try:
                raw_file.unlink()
                logger.debug(f"Cleaned up orphaned temp file: {raw_file}")
            except OSError:
                pass

    def _create_notebook(self, deck_name: str) -> str:
        """Create notebook and track for cleanup."""
        notebook_id = self.generator.create_notebook(
            f"Flashcards: {deck_name}"
        )
        self._created_notebooks.append(notebook_id)
        return notebook_id

    def _add_pdf_source(self, notebook_id: str, pdf_path: Path) -> str | None:
        """Add PDF source to notebook."""
        try:
            source_id = self.generator.add_source(notebook_id, pdf_path)
            logger.info(f"Source added: {source_id[:8]}...")
            return source_id
        except SourceProcessingError as e:
            logger.error(f"Failed to add PDF: {e}")
            logger.info(f"Notebook preserved: {notebook_id}")
            return None

    def _process_large_pdf(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_path: Path | None = None,
    ) -> Deck | None:
        """Process large PDF by splitting into chunks.

        Each chunk is processed independently in its own notebook, then all
        flashcards are combined into a single deck.
        """
        run = _ChunkRun(
            pdf_path=pdf_path,
            deck_name=deck_name,
            pdf_output_path=pdf_output_path,
            processing_path=source_path or pdf_path,
            request=request,
        )
        return generation_chunk_orchestration.process_large_pdf(self, run)

    def _prepare_resume(self, run: _ChunkRun) -> None:
        """Load compatible resume state or initialize a fresh manifest."""
        repository = self._chunk_state_repository
        if not run.request.resume or repository is None:
            return

        run.resume_dir = self._get_resume_dir(
            run.pdf_output_path, run.pdf_path.stem
        )
        run.state_path = self._get_state_file_path(
            run.pdf_output_path, run.pdf_path.stem
        )
        source_signature = self._compute_source_signature(run.processing_path)
        preparation = prepare_resume(
            repository,
            run.pdf_path,
            run.deck_name,
            source_signature,
            len(run.chunks),
            run.resume_dir,
            run.state_path,
        )
        run.manifest = preparation.manifest
        run.chunk_decks.update(preparation.chunk_decks)
        run.completed_indexes = preparation.completed_indexes
        if preparation.resumed:
            logger.info(
                "Resuming PDF processing: "
                f"{len(run.completed_indexes)} of {len(run.chunks)} chunks "
                "already completed"
            )

    def _process_chunks(self, run: _ChunkRun) -> bool:
        """Process every missing chunk and persist successful results."""
        return generation_chunk_orchestration.process_chunks(
            self, run, CHUNK_DELAY_SECONDS
        )

    def _get_or_process_chunk(
        self, run: _ChunkRun, chunk_index: int, chunk_path: Path
    ) -> Deck | None:
        """Return a resumed chunk or process and persist a pending chunk."""
        task = _ChunkTask(
            chunk_path=chunk_path,
            deck_name=run.deck_name,
            pdf_output_path=run.pdf_output_path,
            request=run.request,
            chunk_index=chunk_index,
            total_chunks=len(run.chunks),
        )
        return generation_chunk_orchestration.get_or_process_chunk(
            self, run, task
        )

    @staticmethod
    def _log_chunk_result(
        chunk_index: int, total_chunks: int, chunk_deck: Deck
    ) -> None:
        """Log the result of one chunk."""
        generation_chunk_orchestration.log_chunk_result(
            chunk_index, total_chunks, chunk_deck
        )

    def _mark_chunk_failed(self, run: _ChunkRun, chunk_index: int) -> None:
        """Persist a failed chunk when resume tracking is enabled."""
        mark_chunk_failed(
            self._chunk_state_repository,
            run.manifest,
            run.state_path,
            chunk_index,
            self._last_chunk_error_message,
        )

    def _save_chunk_completion(
        self, run: _ChunkRun, chunk_index: int, chunk_deck: Deck
    ) -> None:
        """Persist a completed chunk and update its manifest entry."""
        save_chunk_completion(
            self._chunk_state_repository,
            run.manifest,
            run.resume_dir,
            run.state_path,
            chunk_index,
            chunk_deck,
        )

    def _combine_chunk_decks(self, run: _ChunkRun) -> Deck | None:
        """Combine completed chunk decks and apply final deduplication."""
        return generation_quality.combine_chunk_decks(self, run)

    def _apply_quality_filter(self, deck: Deck) -> None:
        """Apply quality filtering to remove trivial and similar cards."""
        generation_quality.apply_quality_filter(self, deck)

    @staticmethod
    def _remove_trivial_cards(
        deck: Deck, quality_filter: QualityFilter
    ) -> tuple[list[Flashcard], int]:
        """Return cards that have enough meaningful content."""
        return generation_quality.remove_trivial_cards(deck, quality_filter)

    @staticmethod
    def _remove_similar_cards(
        cards_to_keep: list[Flashcard], quality_filter: QualityFilter
    ) -> tuple[list[Flashcard], int]:
        """Remove the later card from each similar pair."""
        return generation_quality.remove_similar_cards(
            cards_to_keep, quality_filter
        )

    @staticmethod
    def _similar_card_removal_indices(
        cards_to_keep: list[Flashcard], quality_filter: QualityFilter
    ) -> set[int]:
        return generation_quality.similar_card_removal_indices(
            cards_to_keep, quality_filter
        )

    def _process_chunk(
        self,
        chunk_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        chunk_index: int,
        total_chunks: int,
    ) -> Deck | None:
        """Process a single chunk independently with retry logic."""
        return generation_chunk_retry.process_chunk(
            self,
            chunk_path,
            deck_name,
            pdf_output_path,
            request,
            chunk_index,
            total_chunks,
        )

    def _process_chunk_with_retry(self, task: _ChunkTask) -> Deck | None:
        """Process chunk with exponential backoff retry on rate limit errors."""
        return generation_chunk_retry.process_chunk_with_retry(self, task)

    def _process_chunk_attempt(
        self, task: _ChunkTask, attempt: int, delay_seconds: float
    ) -> _ChunkAttemptResult:
        return generation_chunk_retry.process_chunk_attempt(
            self, task, attempt, delay_seconds
        )

    def _retry_after_chunk_error(
        self,
        error: OSError | RuntimeError,
        attempt: int,
        delay: float,
        chunk_index: int,
        total_chunks: int,
    ) -> float | None:
        """Retry transient chunk errors and return the next delay."""
        return generation_chunk_retry.retry_after_chunk_error(
            self, error, attempt, delay, chunk_index, total_chunks
        )

    @staticmethod
    def _is_transient_chunk_error(error: OSError | RuntimeError) -> bool:
        """Return whether a chunk error is safe to retry."""
        return generation_chunk_retry.is_transient_chunk_error(error)

    def _wait_for_chunk_retry(
        self,
        attempt: int,
        delay: float,
        chunk_index: int,
        total_chunks: int,
        reason: str,
    ) -> float | None:
        """Wait before a retry, returning its bounded next delay."""
        return generation_chunk_retry.wait_for_chunk_retry(
            self, attempt, delay, chunk_index, total_chunks, reason
        )

    def _process_chunk_internal(self, task: _ChunkTask) -> Deck | None:
        return generation_chunk_execution.process_chunk_internal(self, task)

    def _run_chunk_generation(
        self, notebook_id: str, task: _ChunkTask
    ) -> Deck | None:
        """Add a chunk source, generate its artifact, and convert the result."""
        return generation_chunk_execution.run_chunk_generation(
            self, notebook_id, task
        )

    def _generate_chunk_artifact(
        self, notebook_id: str, task: _ChunkTask
    ) -> str | None:
        """Generate an artifact using instructions scoped to one chunk."""
        return generation_chunk_execution.generate_chunk_artifact(
            self, notebook_id, task, self.DEFAULT_INSTRUCTIONS
        )

    def _download_chunk_deck(
        self, notebook_id: str, artifact_id: str, task: _ChunkTask
    ) -> Deck:
        """Download one completed chunk and convert its cards."""
        return generation_chunk_execution.download_chunk_deck(
            self, notebook_id, artifact_id, task
        )

    def _cleanup_chunk_notebook(
        self,
        notebook_id: str | None,
        chunk_index: int,
        total_chunks: int,
    ) -> None:
        """Delete a completed chunk notebook when it is still tracked."""
        generation_chunk_execution.cleanup_chunk_notebook(
            self, notebook_id, chunk_index, total_chunks
        )

    def _process_pdf(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
        source_path: Path | None = None,
    ) -> Deck | None:
        """Process single PDF file."""
        return generation_document_execution.process_pdf(
            self, pdf_path, input_path, output_path, request, source_path
        )

    def _log_pdf_processing_error(
        self,
        error: (GenerationError | OSError | ValueError | RuntimeError),
    ) -> None:
        generation_document_execution.log_pdf_processing_error(self, error)

    @staticmethod
    def _output_deck_exists(pdf_output_path: Path, pdf_stem: str) -> bool:
        """Return whether the expected final CSV already exists."""
        return generation_document_execution.output_deck_exists(
            pdf_output_path, pdf_stem
        )

    @staticmethod
    def _log_pdf_header(
        pdf_path: Path, input_path: Path, deck_name: str
    ) -> None:
        """Log the source currently being processed."""
        generation_document_execution.log_pdf_header(
            pdf_path, input_path, deck_name
        )

    def _process_pdf_content(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None:
        """Choose chunked or regular processing for one PDF."""
        return generation_document_execution.process_pdf_content(
            self,
            pdf_path,
            deck_name,
            pdf_output_path,
            request,
            processing_path,
        )

    def _should_chunk_pdf(self, pdf_path: Path, processing_path: Path) -> bool:
        """Return whether the source is a PDF above the chunking threshold."""
        return generation_document_execution.should_chunk_pdf(
            self, pdf_path, processing_path
        )

    def _process_regular_pdf(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None:
        """Generate cards for a source that does not need chunking."""
        return generation_document_execution.process_regular_pdf(
            self,
            pdf_path,
            deck_name,
            pdf_output_path,
            request,
            processing_path,
        )

    def _generate_flashcards(
        self,
        notebook_id: str,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck | None:
        """Generate flashcards for notebook."""
        return generation_artifact_execution.generate_flashcards(
            self,
            notebook_id,
            deck_name,
            pdf_output_path,
            request,
            pdf_stem,
            self.DEFAULT_INSTRUCTIONS,
        )

    def _handle_artifact_completion(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck | None:
        """Handle artifact completion or wait."""
        return generation_artifact_execution.handle_artifact_completion(
            self,
            notebook_id,
            artifact_id,
            output_path,
            deck_name,
            request,
            pdf_stem,
        )

    def _download_and_convert(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        pdf_stem: str = "",
    ) -> Deck:
        """Download and convert flashcards."""
        return generation_artifact_execution.download_and_convert(
            self,
            notebook_id,
            artifact_id,
            output_path,
            deck_name,
            pdf_stem,
        )

    def _download_flashcards(
        self, notebook_id: str, artifact_id: str, json_path: Path
    ) -> list[Flashcard]:
        """Download and parse cards, always removing the raw artifact."""
        return generation_artifact_execution.download_flashcards(
            self, notebook_id, artifact_id, json_path
        )

    @staticmethod
    def _cleanup_raw_file(json_path: Path) -> None:
        """Remove one temporary raw JSON file."""
        generation_artifact_execution.cleanup_raw_file(json_path)

    def _convert_flashcards(
        self, flashcards: list[Flashcard], deck_name: str
    ) -> list[Flashcard]:
        """Convert source cards to cloze cards and attach the deck tag."""
        return generation_artifact_execution.convert_flashcards(
            self, flashcards, deck_name
        )

    def _build_deck(
        self,
        notebook_id: str,
        deck_name: str,
        flashcards: list[Flashcard],
    ) -> Deck:
        """Build and deduplicate a generated deck."""
        return generation_artifact_execution.build_deck(
            self, notebook_id, deck_name, flashcards
        )

    def _delete_completed_notebook(self, notebook_id: str) -> None:
        """Delete a notebook after its artifact was downloaded."""
        generation_artifact_execution.delete_completed_notebook(
            self, notebook_id
        )

    def _save_deck(self, deck: Deck, output_path: Path, pdf_stem: str) -> None:
        """Save deck to output directory."""
        csv_path = output_path / _safe_filename(pdf_stem, ".csv")
        self.exporter.export_csv(deck, csv_path)
        logger.info(f"Saved to: {csv_path}")
