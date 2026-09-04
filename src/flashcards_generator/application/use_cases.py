"""Generate flashcards use case with dependency injection."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import time
from contextlib import AbstractContextManager, ExitStack, nullcontext, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from flashcards_generator.application.converter import ClozeConverter
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.exporter import DeckExporter
from flashcards_generator.domain.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
    Flashcard,
)
from flashcards_generator.domain.exceptions import (
    GenerationError,
    NotebookCleanupError,
    SourceProcessingError,
)
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from flashcards_generator.infrastructure.chunk_state_repository import (
    FileSystemChunkStateRepository,
)
from flashcards_generator.infrastructure.logging_config import get_logger
from flashcards_generator.infrastructure.pdf_utils import PDFChunker
from flashcards_generator.infrastructure.semantic_chunker import QualityFilter

if TYPE_CHECKING:
    from flashcards_generator.domain.ports import ChunkStatePort

# Explicit runtime usage to prevent type-checking-only false positives
_ = (Path, GenerateFlashcardsRequest)

logger = get_logger("use_cases")

# Constants for file handling and timeouts
MAX_FILENAME_LEN = (
    50  # Conservative limit for temp files in nested directories
)
SOURCE_WAIT_TIMEOUT = 600  # seconds
PDF_CHUNKING_THRESHOLD = 50  # Only chunk PDFs with more than 50 pages
MIN_CARDS_QUALITY_LENGTH = 10  # minimum characters for valid card
BORDER_LENGTH = 60  # characters for border lines
CHUNK_DELAY_SECONDS = 5
CHUNK_RETRY_MAX_ATTEMPTS = 3
CHUNK_RETRY_INITIAL_DELAY = 5
CHUNK_RETRY_MAX_DELAY = 60
CHUNK_RETRY_BACKOFF_MULTIPLIER = 2.0


@dataclass(slots=True)
class _ChunkRun:
    """Mutable state for one chunked document generation run."""

    pdf_path: Path
    deck_name: str
    pdf_output_path: Path
    processing_path: Path
    request: GenerateFlashcardsRequest
    chunks: list[Path] = field(default_factory=list)
    chunk_decks: dict[int, Deck] = field(default_factory=dict)
    manifest: ChunkResumeManifest | None = None
    completed_indexes: set[int] = field(default_factory=set)
    resume_dir: Path | None = None
    state_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _ChunkTask:
    """Immutable inputs for processing one document chunk."""

    chunk_path: Path
    deck_name: str
    pdf_output_path: Path
    request: GenerateFlashcardsRequest
    chunk_index: int
    total_chunks: int


def _safe_filename(base_name: str, suffix: str = "") -> str:
    """Create a safe filename that doesn't exceed filesystem limits.

    Args:
        base_name: The base name of the file
        suffix: Optional suffix to append (e.g., "_raw.json")

    Returns:
        A filename that's guaranteed to be within filesystem limits
    """
    total_len = len(base_name) + len(suffix)

    if total_len <= MAX_FILENAME_LEN:
        return f"{base_name}{suffix}"

    # Need to truncate - use hash to preserve uniqueness
    # Format: <truncated>_<hash><suffix>
    hash_len = 8
    separator_len = 1  # for "_"
    available = MAX_FILENAME_LEN - len(suffix) - hash_len - separator_len

    truncated = base_name[:available]
    name_hash = hashlib.md5(base_name.encode()).hexdigest()[:hash_len]

    return f"{truncated}_{name_hash}{suffix}"


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
        converter: ClozeConverter | None = None,
        exporter: DeckExporter | None = None,
        pdf_chunker: PDFChunker | None = None,
        chunk_state_repository: ChunkStatePort | None = None,
    ):
        self.generator = generator
        self.converter = converter or ClozeConverter()
        self.exporter = exporter or DeckExporter()
        self.pdf_chunker = pdf_chunker or PDFChunker()
        self._chunk_state_repository = chunk_state_repository
        self._created_notebooks: list[str] = []
        self._last_chunk_error_message: str | None = None
        self._last_pdf_had_error = False
        self._last_run_had_errors = False

    @property
    def last_run_had_errors(self) -> bool:
        """Whether the latest execution failed to process a source."""
        return self._last_run_had_errors

    def execute(self, request: GenerateFlashcardsRequest) -> list[Deck]:
        """Execute flashcard generation for all PDFs in input directory.

        Args:
            request: Configuration and paths for generation

        Returns:
            List of generated decks
        """
        input_path = request.input_dir.resolve(strict=True)
        output_path = request.output_dir
        output_path.mkdir(parents=True, exist_ok=True)
        output_path = output_path.resolve(strict=True)
        self._cleanup_orphaned_raw_files(output_path)
        self._last_run_had_errors = False

        decks: list[Deck] = []
        try:
            all_pdfs = self._find_all_pdfs(input_path, request)
            if not all_pdfs:
                logger.warning(f"No PDFs found in {input_path}")
                return decks

            logger.info(f"{len(all_pdfs)} PDF(s) found")
            for pdf_path in sorted(all_pdfs):
                self._last_pdf_had_error = False
                deck = self._process_pdf_entry(
                    pdf_path, input_path, output_path, request
                )
                self._last_run_had_errors |= self._last_pdf_had_error
                if deck:
                    decks.append(deck)
            return decks
        finally:
            self._cleanup_notebooks()

    def _process_pdf_entry(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> Deck | None:
        """Process one discovered source while cleaning its snapshot."""
        if not self._is_safe_file_path(pdf_path, input_path):
            return None

        pdf_output_path = self._get_output_subdir(
            pdf_path, input_path, output_path
        )
        source_snapshot = self._snapshot_source(pdf_path, pdf_output_path)
        if source_snapshot is None:
            self._last_pdf_had_error = True
            return None

        try:
            return self._process_pdf_with_lock(
                pdf_path,
                input_path,
                output_path,
                pdf_output_path,
                request,
                source_snapshot,
            )
        finally:
            self._cleanup_source_snapshot(source_snapshot)

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
        resume_lock = self._get_resume_lock(
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

            deck = self._process_pdf(
                pdf_path, input_path, output_path, request, source_snapshot
            )
            self._save_completed_deck(
                deck, pdf_output_path, pdf_path.stem, request
            )
            return deck

    def _save_completed_deck(
        self,
        deck: Deck | None,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> None:
        """Persist a completed, wait-mode deck and clear its resume state."""
        if deck is None or not request.wait_for_completion:
            return
        self._save_deck(deck, pdf_output_path, pdf_stem)
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

    @staticmethod
    def _cleanup_source_snapshot(source_snapshot: Path) -> None:
        """Remove a temporary source snapshot and its empty directory."""
        source_snapshot.unlink(missing_ok=True)
        with suppress(OSError):
            source_snapshot.parent.rmdir()

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
        return resume_dir / f"chunk_{chunk_index:03d}.json"

    def _compute_source_signature(self, pdf_path: Path) -> str:
        """Compute a content signature for resume validation."""
        digest = hashlib.sha256()
        with pdf_path.open("rb") as source_file:
            for block in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(block)
        return f"sha256:{digest.hexdigest()}"

    def _snapshot_source(
        self, pdf_path: Path, pdf_output_path: Path
    ) -> Path | None:
        """Copy a validated source through a no-follow descriptor."""
        source_fd: int | None = None
        snapshot_dir_fd: int | None = None
        snapshot_fd: int | None = None
        snapshot_path: Path | None = None
        try:
            source_fd = os.open(pdf_path, os.O_RDONLY | os.O_NOFOLLOW)
            snapshot_dir = pdf_output_path / ".flashcards_sources"
            self._validate_source_fd(source_fd, pdf_path)
            snapshot_dir_fd = self._open_snapshot_directory(
                snapshot_dir, pdf_output_path
            )
            snapshot_fd, snapshot_path = self._allocate_snapshot(
                snapshot_dir_fd, snapshot_dir, pdf_path
            )
            source_fd_to_copy = source_fd
            snapshot_fd_to_copy = snapshot_fd
            source_fd = None
            snapshot_fd = None
            self._copy_snapshot(source_fd_to_copy, snapshot_fd_to_copy)
            return snapshot_path
        except OSError as error:
            logger.warning(
                f"Skipping changed or unsafe input {pdf_path}: {error}"
            )
            if snapshot_path:
                snapshot_path.unlink(missing_ok=True)
            return None
        finally:
            if source_fd is not None:
                os.close(source_fd)
            if snapshot_fd is not None:
                os.close(snapshot_fd)
            if snapshot_dir_fd is not None:
                os.close(snapshot_dir_fd)

    @staticmethod
    def _validate_source_fd(source_fd: int, pdf_path: Path) -> None:
        """Reject empty or non-regular source descriptors."""
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size == 0:
            raise OSError(f"Input is not a nonempty regular file: {pdf_path}")

    @staticmethod
    def _open_snapshot_directory(snapshot_dir: Path, output_path: Path) -> int:
        """Open the source snapshot directory without following symlinks."""
        try:
            snapshot_dir.mkdir(mode=0o700)
        except FileExistsError:
            pass

        snapshot_mode = snapshot_dir.lstat().st_mode
        if stat.S_ISLNK(snapshot_mode) or not stat.S_ISDIR(snapshot_mode):
            raise OSError(
                f"Snapshot directory is not a real directory: {snapshot_dir}"
            )

        output_root = output_path.resolve(strict=True)
        resolved_snapshot_dir = snapshot_dir.resolve(strict=True)
        try:
            resolved_snapshot_dir.relative_to(output_root)
        except ValueError as error:
            raise OSError(
                f"Snapshot directory escaped output root: {snapshot_dir}"
            ) from error

        snapshot_dir_fd = os.open(
            snapshot_dir,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        os.fchmod(snapshot_dir_fd, 0o700)
        return snapshot_dir_fd

    @staticmethod
    def _allocate_snapshot(
        snapshot_dir_fd: int, snapshot_dir: Path, pdf_path: Path
    ) -> tuple[int, Path]:
        """Allocate an exclusive temporary snapshot file."""
        for _ in range(3):
            snapshot_name = (
                f".{pdf_path.stem}.{secrets.token_hex(16)}"
                f"{pdf_path.suffix.lower()}"
            )
            try:
                snapshot_fd = os.open(
                    snapshot_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=snapshot_dir_fd,
                )
                return snapshot_fd, snapshot_dir / snapshot_name
            except FileExistsError:
                continue
        raise OSError("Could not allocate a unique source snapshot")

    @staticmethod
    def _copy_snapshot(source_fd: int, snapshot_fd: int) -> None:
        """Copy a source descriptor to a durable snapshot descriptor."""
        try:
            with ExitStack() as stack:
                source_file = stack.enter_context(
                    os.fdopen(source_fd, "rb", closefd=False)
                )
                snapshot_file = stack.enter_context(
                    os.fdopen(snapshot_fd, "wb", closefd=False)
                )
                while block := source_file.read(1024 * 1024):
                    snapshot_file.write(block)
                snapshot_file.flush()
                os.fsync(snapshot_file.fileno())
        finally:
            with suppress(OSError):
                os.close(source_fd)
            with suppress(OSError):
                os.close(snapshot_fd)

    def _resume_lock(
        self,
        pdf_output_path: Path,
        pdf_stem: str,
        request: GenerateFlashcardsRequest,
    ) -> AbstractContextManager[bool]:
        """Return exclusive resume ownership when the filesystem port supports it."""
        repository = self._chunk_state_repository
        if request.resume and isinstance(
            repository, FileSystemChunkStateRepository
        ):
            return repository.resume_lock(
                self._get_resume_dir(pdf_output_path, pdf_stem)
            )
        return nullcontext(True)

    def _manifest_matches_source(
        self,
        manifest: ChunkResumeManifest | None,
        pdf_path: Path,
        deck_name: str,
        source_signature: str,
        total_chunks: int,
    ) -> bool:
        """Accept only a manifest for this exact source and chunk layout."""
        return bool(
            manifest
            and manifest.source_pdf == str(pdf_path)
            and manifest.deck_name == deck_name
            and manifest.source_signature == source_signature
            and manifest.total_chunks == total_chunks
        )

    def _load_completed_chunks(
        self,
        manifest: ChunkResumeManifest,
        resume_dir: Path,
        total_chunks: int,
        chunk_decks: dict[int, Deck],
    ) -> set[int]:
        """Load only completed chunks whose derived result files are valid."""
        if not self._chunk_state_repository:
            return set()

        index_counts = self._count_manifest_indexes(manifest)
        completed_indexes: set[int] = set()
        for chunk_state in manifest.chunks:
            chunk_index = chunk_state.chunk_index
            expected_path = self._get_chunk_result_path(
                resume_dir, chunk_index
            )
            if not self._is_valid_completed_chunk(
                chunk_state, expected_path, index_counts, total_chunks
            ):
                self._warn_for_invalid_completed_chunk(chunk_state)
                continue

            chunk_deck = self._load_valid_chunk_result(
                expected_path, chunk_state.card_count, chunk_index
            )
            if chunk_deck is None:
                continue

            chunk_decks[chunk_index] = chunk_deck
            completed_indexes.add(chunk_index)

        return completed_indexes

    @staticmethod
    def _count_manifest_indexes(
        manifest: ChunkResumeManifest,
    ) -> dict[int, int]:
        """Count manifest entries so duplicate chunk indexes are rejected."""
        index_counts: dict[int, int] = {}
        for chunk_state in manifest.chunks:
            index_counts[chunk_state.chunk_index] = (
                index_counts.get(chunk_state.chunk_index, 0) + 1
            )
        return index_counts

    @staticmethod
    def _is_valid_completed_chunk(
        chunk_state: ChunkState,
        expected_path: Path,
        index_counts: dict[int, int],
        total_chunks: int,
    ) -> bool:
        """Check the manifest invariants for one completed chunk."""
        return (
            index_counts[chunk_state.chunk_index] == 1
            and 1 <= chunk_state.chunk_index <= total_chunks
            and chunk_state.status == ChunkStatus.COMPLETED
            and chunk_state.result_path == str(expected_path)
        )

    @staticmethod
    def _warn_for_invalid_completed_chunk(
        chunk_state: ChunkState,
    ) -> None:
        """Log only completed entries that failed manifest validation."""
        if chunk_state.status == ChunkStatus.COMPLETED:
            logger.warning(
                "Ignoring invalid saved result for chunk "
                f"{chunk_state.chunk_index}"
            )

    def _load_valid_chunk_result(
        self,
        result_path: Path,
        expected_card_count: int,
        chunk_index: int,
    ) -> Deck | None:
        """Load a result and verify its card count against the manifest."""
        repository = self._chunk_state_repository
        if repository is None:
            return None

        try:
            chunk_deck = repository.load_chunk_result(result_path)
            if len(chunk_deck.flashcards) != expected_card_count:
                raise ValueError("saved card count does not match manifest")
        except (OSError, ValueError) as error:
            logger.warning(
                f"Regenerating unavailable result for chunk {chunk_index}: "
                f"{error}"
            )
            return None
        return chunk_deck

    def _build_resume_manifest(
        self,
        pdf_path: Path,
        deck_name: str,
        total_chunks: int,
        source_signature: str,
    ) -> ChunkResumeManifest:
        """Create a fresh manifest for resume-enabled chunk processing."""
        now = datetime.now(timezone.utc)
        return ChunkResumeManifest(
            source_pdf=str(pdf_path),
            source_signature=source_signature,
            deck_name=deck_name,
            total_chunks=total_chunks,
            chunks=[],
            created_at=now,
            updated_at=now,
        )

    def _set_chunk_state(
        self,
        manifest: ChunkResumeManifest,
        chunk_index: int,
        status: ChunkStatus,
        *,
        card_count: int = 0,
        result_path: Path | None = None,
        error_message: str | None = None,
    ) -> None:
        """Upsert manifest state for a single chunk."""
        now = datetime.now(timezone.utc)
        state = ChunkState(
            chunk_index=chunk_index,
            status=status,
            card_count=card_count,
            result_path=str(result_path) if result_path else None,
            updated_at=now,
            error_message=error_message,
        )

        for index, existing_state in enumerate(manifest.chunks):
            if existing_state.chunk_index == chunk_index:
                manifest.chunks[index] = state
                break
        else:
            manifest.chunks.append(state)

        manifest.updated_at = now

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
        """Find all PDF files recursively with filtering."""
        if request.explicit_files:
            return self._find_explicit_files(input_path, request)

        all_files = self._find_supported_files(input_path)
        return self._apply_file_filters(
            all_files, request.include_pattern, request.exclude_pattern
        )

    def _find_explicit_files(
        self,
        input_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> list[Path]:
        """Resolve and validate the explicitly requested source files."""
        pdf_paths: list[Path] = []
        for file_name in request.explicit_files:
            file_path = input_path / file_name
            if self._is_safe_file_path(file_path, input_path):
                pdf_paths.append(file_path.resolve(strict=True))
            else:
                logger.warning(f"Explicit file not found: {file_name}")
        return pdf_paths

    def _find_supported_files(self, input_path: Path) -> list[Path]:
        """Find safe PDF and PPTX files beneath the input directory."""
        return self._find_files_by_pattern(
            input_path, "*.pdf"
        ) + self._find_files_by_pattern(input_path, "*.pptx")

    def _find_files_by_pattern(
        self, input_path: Path, pattern: str
    ) -> list[Path]:
        """Find safe input files matching one extension pattern."""
        return [
            file_path
            for file_path in input_path.rglob(pattern)
            if self._is_safe_file_path(file_path, input_path)
        ]

    @staticmethod
    def _apply_file_filters(
        files: list[Path],
        include_pattern: str | None,
        exclude_pattern: str | None,
    ) -> list[Path]:
        """Apply optional filename filters in their documented order."""
        filtered_files = files
        if include_pattern:
            filtered_files = GenerateFlashcardsUseCase._filter_files(
                filtered_files, include_pattern, exclude=False
            )
            logger.info(f"Include filter '{include_pattern}' applied")

        if exclude_pattern:
            filtered_files = GenerateFlashcardsUseCase._filter_files(
                filtered_files, exclude_pattern, exclude=True
            )
            logger.info(f"Exclude filter '{exclude_pattern}' applied")
        return filtered_files

    @staticmethod
    def _filter_files(
        files: list[Path], pattern: str, *, exclude: bool
    ) -> list[Path]:
        """Filter files by name, optionally retaining non-matches."""
        import fnmatch

        if exclude:
            return [
                file_path
                for file_path in files
                if not fnmatch.fnmatch(file_path.name, pattern)
            ]
        return [
            file_path
            for file_path in files
            if fnmatch.fnmatch(file_path.name, pattern)
        ]

    # Supported file extensions that NotebookLM can process
    SUPPORTED_EXTENSIONS: ClassVar[set[str]] = {".pdf", ".pptx"}

    def _is_safe_file_path(self, file_path: Path, input_path: Path) -> bool:
        """Validate that file is safe to process and has supported extension."""
        try:
            # Reject symlinks to prevent path traversal attacks
            if file_path.is_symlink():
                logger.warning(f"Skipping symlink: {file_path}")
                return False

            # Use strict=True to ensure path exists before resolving
            resolved_file = file_path.resolve(strict=True)
            resolved_input = input_path.resolve(strict=True)
            return self._is_valid_resolved_file(
                resolved_file, resolved_input, file_path
            )
        except (OSError, ValueError) as e:
            logger.warning(f"Skipping invalid file path {file_path}: {e}")
            return False

    def _is_valid_resolved_file(
        self,
        resolved_file: Path,
        resolved_input: Path,
        original_path: Path,
    ) -> bool:
        """Validate containment, file type, extension, and nonempty content."""
        if not self._is_within_input(
            resolved_file, resolved_input, original_path
        ):
            return False
        if not resolved_file.is_file():
            logger.warning(f"Skipping non-file path: {original_path}")
            return False
        if resolved_file.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            logger.warning(f"Skipping unsupported file type: {original_path}")
            return False
        if resolved_file.stat().st_size == 0:
            logger.warning(f"Skipping empty file: {original_path}")
            return False
        return True

    @staticmethod
    def _is_within_input(
        resolved_file: Path,
        resolved_input: Path,
        original_path: Path,
    ) -> bool:
        """Return whether a resolved path stays within the input root."""
        try:
            resolved_file.relative_to(resolved_input)
        except ValueError:
            logger.warning(
                f"Skipping file outside input directory: {original_path}"
            )
            return False
        return True

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str:
        """Generate deck name from PDF path."""
        relative_path = pdf_path.relative_to(input_path)
        name_parts = [*list(relative_path.parent.parts), relative_path.stem]
        return "_".join(name_parts)

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        relative_path = pdf_path.relative_to(input_path)
        result_root = output_path.resolve(strict=True)
        parent = relative_path.parent
        subdir = result_root if parent == Path(".") else result_root / parent
        subdir.mkdir(parents=True, exist_ok=True)
        resolved_subdir = subdir.resolve(strict=True)
        try:
            resolved_subdir.relative_to(result_root)
        except ValueError as error:
            raise OSError(
                f"Output path escaped result root: {subdir}"
            ) from error
        return resolved_subdir

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

        try:
            run.chunks = list(
                self.pdf_chunker.chunk_pdf(
                    run.processing_path,
                    run.pdf_output_path / ".temp_chunks",
                )
            )
            total_chunks = len(run.chunks)
            logger.info(f"Processing {total_chunks} chunks independently...")
            self._prepare_resume(run)
            if not self._process_chunks(run):
                return None
            return self._combine_chunk_decks(run)

        finally:
            if not run.request.resume:
                self.pdf_chunker.cleanup_chunks(run.chunks)

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
        existing_manifest = self._load_resume_manifest(
            repository, run.state_path, run.resume_dir
        )
        if self._manifest_matches_source(
            existing_manifest,
            run.pdf_path,
            run.deck_name,
            source_signature,
            len(run.chunks),
        ):
            assert existing_manifest is not None
            run.manifest = existing_manifest
            run.completed_indexes = self._load_completed_chunks(
                existing_manifest,
                run.resume_dir,
                len(run.chunks),
                run.chunk_decks,
            )
            logger.info(
                "Resuming PDF processing: "
                f"{len(run.completed_indexes)} of {len(run.chunks)} chunks "
                "already completed"
            )
            return

        repository.delete_chunk_results(run.resume_dir)
        run.manifest = self._build_resume_manifest(
            run.pdf_path,
            run.deck_name,
            len(run.chunks),
            source_signature,
        )
        repository.save_manifest(run.state_path, run.manifest)

    @staticmethod
    def _load_resume_manifest(
        repository: ChunkStatePort,
        state_path: Path,
        resume_dir: Path,
    ) -> ChunkResumeManifest | None:
        """Load a manifest and remove corrupted persisted resume state."""
        try:
            return repository.load_manifest(state_path)
        except (OSError, ValueError) as error:
            logger.warning(f"Discarding corrupt resume manifest: {error}")
            repository.delete_manifest(state_path)
            repository.delete_chunk_results(resume_dir)
            return None

    def _process_chunks(self, run: _ChunkRun) -> bool:
        """Process every missing chunk and persist successful results."""
        for chunk_index, chunk_path in enumerate(run.chunks, 1):
            chunk_deck = self._get_or_process_chunk(
                run, chunk_index, chunk_path
            )
            if chunk_deck is None:
                return False
            self._log_chunk_result(chunk_index, len(run.chunks), chunk_deck)
            if chunk_index < len(run.chunks):
                logger.debug(
                    f"Waiting {CHUNK_DELAY_SECONDS}s before next chunk..."
                )
                time.sleep(CHUNK_DELAY_SECONDS)
        return True

    def _get_or_process_chunk(
        self, run: _ChunkRun, chunk_index: int, chunk_path: Path
    ) -> Deck | None:
        """Return a resumed chunk or process and persist a pending chunk."""
        if chunk_index in run.completed_indexes:
            logger.info(
                f"Skipping chunk {chunk_index}/{len(run.chunks)} - already done"
            )
            return run.chunk_decks[chunk_index]

        logger.info(f"Processing chunk {chunk_index}/{len(run.chunks)}...")
        self._last_chunk_error_message = None
        chunk_deck = self._process_chunk(
            chunk_path,
            run.deck_name,
            run.pdf_output_path,
            run.request,
            chunk_index,
            len(run.chunks),
        )
        if chunk_deck is None:
            self._mark_chunk_failed(run, chunk_index)
            return None

        run.chunk_decks[chunk_index] = chunk_deck
        self._save_chunk_completion(run, chunk_index, chunk_deck)
        return chunk_deck

    @staticmethod
    def _log_chunk_result(
        chunk_index: int, total_chunks: int, chunk_deck: Deck
    ) -> None:
        """Log the result of one chunk."""
        if chunk_deck.flashcards:
            logger.info(
                f"Chunk {chunk_index}/{total_chunks}: "
                f"{len(chunk_deck.flashcards)} flashcards"
            )
        else:
            logger.warning(
                f"Chunk {chunk_index}/{total_chunks}: no flashcards generated"
            )

    def _mark_chunk_failed(self, run: _ChunkRun, chunk_index: int) -> None:
        """Persist a failed chunk when resume tracking is enabled."""
        repository = self._chunk_state_repository
        if (
            run.manifest is None
            or run.state_path is None
            or repository is None
        ):
            return

        self._set_chunk_state(
            run.manifest,
            chunk_index,
            ChunkStatus.FAILED,
            error_message=(
                self._last_chunk_error_message or "Chunk processing failed"
            ),
        )
        repository.save_manifest(run.state_path, run.manifest)

    def _save_chunk_completion(
        self, run: _ChunkRun, chunk_index: int, chunk_deck: Deck
    ) -> None:
        """Persist a completed chunk and update its manifest entry."""
        repository = self._chunk_state_repository
        if (
            run.manifest is None
            or run.resume_dir is None
            or run.state_path is None
            or repository is None
        ):
            return

        chunk_result_path = self._get_chunk_result_path(
            run.resume_dir, chunk_index
        )
        repository.save_chunk_result(chunk_result_path, chunk_deck)
        self._set_chunk_state(
            run.manifest,
            chunk_index,
            ChunkStatus.COMPLETED,
            card_count=len(chunk_deck.flashcards),
            result_path=chunk_result_path,
        )
        repository.save_manifest(run.state_path, run.manifest)

    def _combine_chunk_decks(self, run: _ChunkRun) -> Deck | None:
        """Combine completed chunk decks and apply final deduplication."""
        all_flashcards = [
            card
            for chunk_index in range(1, len(run.chunks) + 1)
            for card in run.chunk_decks[chunk_index].flashcards
        ]
        if not all_flashcards:
            logger.error("No flashcards generated from any chunk")
            return None

        logger.info(f"Total flashcards from all chunks: {len(all_flashcards)}")
        combined_deck = Deck(
            name=run.deck_name,
            description=f"Deck de {run.deck_name} ({len(run.chunks)} chunks)",
            flashcards=all_flashcards,
            notebook_id="",
        )
        removed = combined_deck.deduplicate(similarity_threshold=0.85)
        if removed > 0:
            logger.info(f"Removed {removed} duplicate flashcards")
        self._apply_quality_filter(combined_deck)
        return combined_deck

    def _apply_quality_filter(self, deck: Deck) -> None:
        """Apply quality filtering to remove trivial and similar cards."""
        if not deck.flashcards:
            return

        quality_filter = QualityFilter()
        cards_to_keep, trivial_count = self._remove_trivial_cards(
            deck, quality_filter
        )
        if trivial_count > 0:
            logger.info(
                f"Quality filter removed {trivial_count} trivial cards"
            )

        cards_to_keep, removed_similar = self._remove_similar_cards(
            cards_to_keep, quality_filter
        )
        if removed_similar > 0:
            logger.info(
                f"Quality filter removed {removed_similar} similar cards"
            )
        deck.flashcards = cards_to_keep

    @staticmethod
    def _remove_trivial_cards(
        deck: Deck, quality_filter: QualityFilter
    ) -> tuple[list[Flashcard], int]:
        """Return cards that have enough meaningful content."""
        cards_to_keep = []
        trivial_count = 0
        for card in deck.flashcards:
            if quality_filter.is_trivial(card.front, card.back):
                trivial_count += 1
            else:
                cards_to_keep.append(card)
        return cards_to_keep, trivial_count

    @staticmethod
    def _remove_similar_cards(
        cards_to_keep: list[Flashcard], quality_filter: QualityFilter
    ) -> tuple[list[Flashcard], int]:
        """Remove the later card from each similar pair."""
        if len(cards_to_keep) < 2:
            return cards_to_keep, 0

        card_tuples = [(card.front, card.back) for card in cards_to_keep]
        similar_pairs = quality_filter.find_similar_cards(card_tuples)
        indices_to_remove = {j for _, j, _ in similar_pairs}
        filtered_cards = [
            card
            for index, card in enumerate(cards_to_keep)
            if index not in indices_to_remove
        ]
        return filtered_cards, len(cards_to_keep) - len(filtered_cards)

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
        return self._process_chunk_with_retry(
            chunk_path,
            deck_name,
            pdf_output_path,
            request,
            chunk_index,
            total_chunks,
        )

    def _process_chunk_with_retry(
        self,
        chunk_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        chunk_index: int,
        total_chunks: int,
    ) -> Deck | None:
        """Process chunk with exponential backoff retry on rate limit errors."""
        delay = float(CHUNK_RETRY_INITIAL_DELAY)
        self._last_chunk_error_message = None

        for attempt in range(1, CHUNK_RETRY_MAX_ATTEMPTS + 1):
            try:
                result = self._process_chunk_internal(
                    chunk_path,
                    deck_name,
                    pdf_output_path,
                    request,
                    chunk_index,
                    total_chunks,
                )
            except (OSError, RuntimeError) as error:
                next_delay = self._retry_after_chunk_error(
                    error, attempt, delay, chunk_index, total_chunks
                )
                if next_delay is None:
                    return None
                delay = next_delay
                continue

            if result is not None:
                self._last_chunk_error_message = None
                return result

            next_delay = self._wait_for_chunk_retry(
                attempt,
                delay,
                chunk_index,
                total_chunks,
                "retry",
            )
            if next_delay is None:
                self._last_chunk_error_message = (
                    "Chunk processing returned no result after retries"
                )
                return None
            delay = next_delay

        return None

    def _retry_after_chunk_error(
        self,
        error: OSError | RuntimeError,
        attempt: int,
        delay: float,
        chunk_index: int,
        total_chunks: int,
    ) -> float | None:
        """Retry transient chunk errors and return the next delay."""
        self._last_chunk_error_message = str(error)
        if not self._is_transient_chunk_error(error):
            logger.error(f"Chunk {chunk_index}/{total_chunks}: {error}")
            return None

        next_delay = self._wait_for_chunk_retry(
            attempt,
            delay,
            chunk_index,
            total_chunks,
            "transient error, retry",
        )
        if next_delay is None:
            logger.error(f"Chunk {chunk_index}/{total_chunks}: {error}")
        return next_delay

    @staticmethod
    def _is_transient_chunk_error(error: OSError | RuntimeError) -> bool:
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

    @staticmethod
    def _wait_for_chunk_retry(
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
        time.sleep(delay)
        return min(
            delay * CHUNK_RETRY_BACKOFF_MULTIPLIER,
            CHUNK_RETRY_MAX_DELAY,
        )

    def _process_chunk_internal(
        self,
        chunk_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        chunk_index: int,
        total_chunks: int,
    ) -> Deck | None:
        chunk_notebook_id: str | None = None
        try:
            task = _ChunkTask(
                chunk_path=chunk_path,
                deck_name=deck_name,
                pdf_output_path=pdf_output_path,
                request=request,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
            )
            chunk_deck_name = f"{task.deck_name}_chunk{task.chunk_index}"
            chunk_notebook_id = self._create_notebook(chunk_deck_name)
            return self._run_chunk_generation(chunk_notebook_id, task)
        except (
            GenerationError,
            SourceProcessingError,
            OSError,
            RuntimeError,
        ) as e:
            logger.error(f"Chunk {chunk_index}/{total_chunks}: error - {e}")
            raise
        finally:
            self._cleanup_chunk_notebook(
                chunk_notebook_id, chunk_index, total_chunks
            )

    def _run_chunk_generation(
        self, notebook_id: str, task: _ChunkTask
    ) -> Deck | None:
        """Add a chunk source, generate its artifact, and convert the result."""
        source_id = self._add_pdf_source(notebook_id, task.chunk_path)
        if not source_id:
            logger.error(f"Failed to add chunk {task.chunk_index} as source")
            return None

        logger.info(
            f"Chunk {task.chunk_index}/{task.total_chunks}: "
            "source added, waiting..."
        )
        self.generator.wait_for_source(
            notebook_id, source_id, timeout=SOURCE_WAIT_TIMEOUT
        )
        artifact_id = self._generate_chunk_artifact(notebook_id, task)
        if not artifact_id:
            logger.error(
                f"Chunk {task.chunk_index}/{task.total_chunks}: "
                "failed to generate"
            )
            return None

        completed = self.generator.wait_for_artifact(
            notebook_id, artifact_id, timeout=task.request.timeout
        )
        if not completed:
            logger.warning(
                f"Chunk {task.chunk_index}/{task.total_chunks}: timeout"
            )
            return None
        return self._download_chunk_deck(notebook_id, artifact_id, task)

    def _generate_chunk_artifact(
        self, notebook_id: str, task: _ChunkTask
    ) -> str | None:
        """Generate an artifact using instructions scoped to one chunk."""
        instructions = task.request.instructions or self.DEFAULT_INSTRUCTIONS
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
        return self.generator.generate_flashcards(notebook_id, gen_config)

    def _download_chunk_deck(
        self, notebook_id: str, artifact_id: str, task: _ChunkTask
    ) -> Deck:
        """Download one completed chunk and convert its cards."""
        json_path = task.pdf_output_path / _safe_filename(
            f"chunk{task.chunk_index}", "_raw.json"
        )
        try:
            self.generator.download_flashcards(
                notebook_id, artifact_id, json_path
            )
            flashcards = self.generator.parse_flashcards(json_path)
        finally:
            self._cleanup_raw_file(json_path)

        cloze_cards = self._convert_flashcards(flashcards, task.deck_name)
        return Deck(
            name=f"{task.deck_name}_chunk{task.chunk_index}",
            description=(f"Chunk {task.chunk_index} of {task.total_chunks}"),
            flashcards=cloze_cards,
            notebook_id=notebook_id,
        )

    def _cleanup_chunk_notebook(
        self,
        notebook_id: str | None,
        chunk_index: int,
        total_chunks: int,
    ) -> None:
        """Delete a completed chunk notebook when it is still tracked."""
        if not notebook_id or notebook_id not in self._created_notebooks:
            return
        try:
            self.generator.delete_notebook(notebook_id)
            self._created_notebooks.remove(notebook_id)
            logger.debug(
                f"Chunk {chunk_index}/{total_chunks}: notebook cleaned up"
            )
        except NotebookCleanupError as error:
            logger.warning(f"Failed to cleanup chunk notebook: {error}")

    def _process_pdf(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
        source_path: Path | None = None,
    ) -> Deck | None:
        """Process single PDF file."""
        deck_name = self._get_deck_name(pdf_path, input_path)
        pdf_output_path = self._get_output_subdir(
            pdf_path, input_path, output_path
        )
        if self._output_deck_exists(pdf_output_path, pdf_path.stem):
            logger.info(f"Skipping {pdf_path.name} - CSV already exists")
            return None

        self._log_pdf_header(pdf_path, input_path, deck_name)
        try:
            return self._process_pdf_content(
                pdf_path,
                deck_name,
                pdf_output_path,
                request,
                source_path or pdf_path,
            )
        except GenerationError as e:
            self._last_pdf_had_error = True
            logger.error(f"Generation error: {e}")
            return None
        except (OSError, ValueError, RuntimeError) as e:
            self._last_pdf_had_error = True
            logger.error(f"Processing error: {e}")
            return None
        except (AttributeError, KeyError, TypeError) as e:
            # Catch malformed responses and unexpected object shapes gracefully.
            self._last_pdf_had_error = True
            logger.error(f"Unexpected error processing PDF: {e}")
            return None

    @staticmethod
    def _output_deck_exists(pdf_output_path: Path, pdf_stem: str) -> bool:
        """Return whether the expected final CSV already exists."""
        return (pdf_output_path / _safe_filename(pdf_stem, ".csv")).exists()

    @staticmethod
    def _log_pdf_header(
        pdf_path: Path, input_path: Path, deck_name: str
    ) -> None:
        """Log the source currently being processed."""
        logger.info("=" * BORDER_LENGTH)
        logger.info(f"PDF: {pdf_path.relative_to(input_path)}")
        logger.info(f"Deck: {deck_name}")
        logger.info("=" * BORDER_LENGTH)

    def _process_pdf_content(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None:
        """Choose chunked or regular processing for one PDF."""
        if self._should_chunk_pdf(pdf_path, processing_path):
            logger.info(
                f"Large PDF detected (>{PDF_CHUNKING_THRESHOLD} pages), "
                "using chunking..."
            )
            deck = self._process_large_pdf(
                pdf_path,
                deck_name,
                pdf_output_path,
                request,
                processing_path,
            )
        else:
            deck = self._process_regular_pdf(
                pdf_path,
                deck_name,
                pdf_output_path,
                request,
                processing_path,
            )
        if deck is None:
            self._last_pdf_had_error = True
        return deck

    def _should_chunk_pdf(self, pdf_path: Path, processing_path: Path) -> bool:
        """Return whether the source is a PDF above the chunking threshold."""
        return (
            pdf_path.suffix.lower() == ".pdf"
            and self.pdf_chunker.needs_chunking(
                processing_path, threshold=PDF_CHUNKING_THRESHOLD
            )
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
        notebook_id = self._create_notebook(deck_name)
        source_id = self._add_pdf_source(notebook_id, processing_path)
        if not source_id:
            self._last_pdf_had_error = True
            return None
        logger.info("Processing source...")
        self.generator.wait_for_source(
            notebook_id, source_id, timeout=SOURCE_WAIT_TIMEOUT
        )
        deck = self._generate_flashcards(
            notebook_id, deck_name, pdf_output_path, request, pdf_path.stem
        )
        if deck is None:
            self._last_pdf_had_error = True
        return deck

    def _generate_flashcards(
        self,
        notebook_id: str,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck | None:
        """Generate flashcards for notebook."""
        instructions = request.instructions or self.DEFAULT_INSTRUCTIONS
        gen_config = GenerationConfig(
            difficulty=request.difficulty,
            quantity=request.quantity,
            instructions=instructions,
            timeout_seconds=request.timeout,
            wait_for_completion=request.wait_for_completion,
        )

        logger.info("Generating flashcards...")
        artifact_id = self.generator.generate_flashcards(
            notebook_id, gen_config
        )

        if not artifact_id:
            logger.error("Failed to generate flashcards")
            return None

        return self._handle_artifact_completion(
            notebook_id,
            artifact_id,
            pdf_output_path,
            deck_name,
            request,
            pdf_stem,
        )

    def _handle_artifact_completion(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck:
        """Handle artifact completion or wait."""
        logger.info("Waiting for generation...")

        if not request.wait_for_completion:
            logger.info(f"Background generation. ID: {artifact_id}")
            return Deck(
                name=deck_name,
                description=f"Deck {deck_name} (generating)",
                notebook_id=notebook_id,
            )

        completed = self.generator.wait_for_artifact(
            notebook_id, artifact_id, timeout=request.timeout
        )

        if completed:
            return self._download_and_convert(
                notebook_id, artifact_id, output_path, deck_name, pdf_stem
            )

        logger.warning(f"Timeout. ID: {artifact_id}")
        logger.info(f"Notebook preserved for retry: {notebook_id}")
        return Deck(
            name=deck_name,
            description=f"Deck {deck_name}",
            notebook_id=notebook_id,
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
        # Use pdf_stem for temp file to avoid path duplication with deck_name
        temp_name = pdf_stem if pdf_stem else deck_name
        json_path = output_path / _safe_filename(temp_name, "_raw.json")
        flashcards = self._download_flashcards(
            notebook_id, artifact_id, json_path
        )
        deck = self._build_deck(notebook_id, deck_name, flashcards)
        self._delete_completed_notebook(notebook_id)
        return deck

    def _download_flashcards(
        self, notebook_id: str, artifact_id: str, json_path: Path
    ) -> list[Flashcard]:
        """Download and parse cards, always removing the raw artifact."""
        try:
            self.generator.download_flashcards(
                notebook_id, artifact_id, json_path
            )
            return self.generator.parse_flashcards(json_path)
        finally:
            self._cleanup_raw_file(json_path)

    @staticmethod
    def _cleanup_raw_file(json_path: Path) -> None:
        """Remove one temporary raw JSON file."""
        try:
            json_path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning(f"Failed to cleanup temp file: {error}")

    def _convert_flashcards(
        self, flashcards: list[Flashcard], deck_name: str
    ) -> list[Flashcard]:
        """Convert source cards to cloze cards and attach the deck tag."""
        cloze_cards: list[Flashcard] = []
        tag = deck_name.lower().replace(" ", "_")
        for card in flashcards:
            cloze_card = self.converter.convert(card)
            if cloze_card:
                cloze_card.tags.append(tag)
                cloze_cards.append(cloze_card)
        return cloze_cards

    def _build_deck(
        self,
        notebook_id: str,
        deck_name: str,
        flashcards: list[Flashcard],
    ) -> Deck:
        """Build and deduplicate a generated deck."""
        deck = Deck(
            name=deck_name,
            description=f"Deck de {deck_name}",
            flashcards=self._convert_flashcards(flashcards, deck_name),
            notebook_id=notebook_id,
        )
        removed = deck.deduplicate(similarity_threshold=0.85)
        if removed > 0:
            logger.info(f"Removed {removed} duplicate flashcards")
        return deck

    def _delete_completed_notebook(self, notebook_id: str) -> None:
        """Delete a notebook after its artifact was downloaded."""
        try:
            self.generator.delete_notebook(notebook_id)
            if notebook_id in self._created_notebooks:
                self._created_notebooks.remove(notebook_id)
            logger.info("Notebook deleted")
        except NotebookCleanupError:
            pass

    def _save_deck(self, deck: Deck, output_path: Path, pdf_stem: str) -> None:
        """Save deck to output directory."""
        csv_path = output_path / _safe_filename(pdf_stem, ".csv")
        self.exporter.export_csv(deck, csv_path)
        logger.info(f"Saved to: {csv_path}")
