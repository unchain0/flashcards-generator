"""Generate flashcards use case with dependency injection."""

import hashlib
import time
from pathlib import Path
from typing import ClassVar

from flashcards_generator.application.converter import ClozeConverter
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.exporter import DeckExporter
from flashcards_generator.domain.entities import Deck
from flashcards_generator.domain.exceptions import (
    GenerationError,
    NotebookCleanupError,
    SourceProcessingError,
)
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from flashcards_generator.infrastructure.logging_config import get_logger
from flashcards_generator.infrastructure.pdf_utils import PDFChunker
from flashcards_generator.infrastructure.semantic_chunker import QualityFilter

# Explicit runtime usage to prevent type-checking-only false positives
_ = (Path, GenerateFlashcardsRequest)

logger = get_logger("use_cases")

# Constants for file handling and timeouts
MAX_FILENAME_LEN = 50  # Conservative limit for temp files in nested directories
SOURCE_WAIT_TIMEOUT = 600  # seconds
PDF_CHUNKING_THRESHOLD = 50  # Only chunk PDFs with more than 50 pages
MIN_CARDS_QUALITY_LENGTH = 10  # minimum characters for valid card
BORDER_LENGTH = 60  # characters for border lines
CHUNK_DELAY_SECONDS = 5
CHUNK_RETRY_MAX_ATTEMPTS = 3
CHUNK_RETRY_INITIAL_DELAY = 5
CHUNK_RETRY_MAX_DELAY = 60
CHUNK_RETRY_BACKOFF_MULTIPLIER = 2.0


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
        "Gere flashcards de alta qualidade para estudo com spaced repetition. "
        "FORMATO OBRIGATÓRIO (100% dos cards): Use APENAS Cloze Deletion. "
        "Crie frases completas onde o conceito importante está envolto em "
        "{{c1::conceito}}. "
        "Exemplo: 'Um {{c1::conjunto}} é uma coleção de elementos.' "
        "ETAPA 1 - DECOMPOSIÇÃO: Primeiro decompõe todo o conteúdo em "
        "sentenças declarativas atômicas (uma única ideia por sentença). "
        "ETAPA 2 - GERAÇÃO: Depois converte cada sentença atômica em cloze. "
        "PRINCÍPIOS FUNDAMENTAIS: "
        "1. CONTEXTO AUTO-CONTIDO: Cada card deve conter TODO o contexto "
        "necessário para respondê-lo. NUNCA referencie 'a seção anterior' "
        "ou 'mencionado acima' sem incluir esse contexto no card. "
        "2. ATOMICIDADE: Teste APENAS UM conceito por card. Se uma frase "
        "tem múltiplos conceitos, divida em cards separados. "
        "3. CONTEXTO vs RESPOSTA: Mantenha palavras de categoria/qualificador "
        "(ex: 'design pattern', 'função') visíveis; oculte apenas o fato específico. "
        "4. LISTAS COM CLOZE PROGRESSIVO: Para listas completas, use UM card "
        "com clozes progressivos: {{c1::itemA}} {{c2::itemB}} {{c3::itemC}}. "
        "REGRAS DE CONTEÚDO: "
        "5. NUNCA crie cloze para palavras triviais: é, são, de, da, um, uma. "
        "6. NUNCA inclua sinônimos ou traduções entre parênteses ao lado do cloze. "
        "7. NUNCA crie cards com juízos subjetivos: 'poderoso', 'obsoleto', 'ruim'. "
        "8. Use LaTeX $...$ para matemática. "
        "9. Frases curtas (máximo 20-25 palavras) e diretas. "
        "10. Para AMBIGUIDADE use hints: {{c1::termo::dica_de_contexto}}. "
        "TIPOS ESPECÍFICOS: "
        "- Código: Extraia APENAS o elemento sintático-chave, não blocos inteiros. "
        "- Definições: Use padrão 'Conceito é Descritor'. "
        "- Processos: Teste UMA etapa por card, não sequências inteiras. "
        "CONTEXTO DO DOCUMENTO: "
        "- Este é um trecho de um documento maior. "
        "Gere flashcards APENAS sobre o conteúdo presente nesta seção. "
        "- Se um conceito parecer incompleto ou continuar em outra seção, ignore-o. "
        "- NÃO crie flashcards que dependam de conteúdo de outras partes. "
        "- Foque em fatos e conceitos completos e auto-contidos. "
        "FORMATO: Frente (cloze com contexto); Verso (explicação adicional)"
    )

    def __init__(
        self,
        generator: FlashcardGeneratorPort,
        converter: ClozeConverter | None = None,
        exporter: DeckExporter | None = None,
        pdf_chunker: PDFChunker | None = None,
    ):
        self.generator = generator
        self.converter = converter or ClozeConverter()
        self.exporter = exporter or DeckExporter()
        self.pdf_chunker = pdf_chunker or PDFChunker()
        self._created_notebooks: list[str] = []

    def execute(self, request: GenerateFlashcardsRequest) -> list[Deck]:
        """Execute flashcard generation for all PDFs in input directory.

        Args:
            request: Configuration and paths for generation

        Returns:
            List of generated decks
        """
        input_path = request.input_dir
        output_path = request.output_dir
        output_path.mkdir(parents=True, exist_ok=True)
        self._cleanup_orphaned_raw_files(output_path)

        decks: list[Deck] = []
        try:
            all_pdfs = self._find_all_pdfs(input_path, request)

            if not all_pdfs:
                logger.warning(f"No PDFs found in {input_path}")
                return decks

            logger.info(f"{len(all_pdfs)} PDF(s) found")

            for pdf_path in sorted(all_pdfs):
                pdf_output_path = self._get_output_subdir(
                    pdf_path, input_path, output_path
                )
                deck = self._process_pdf(pdf_path, input_path, output_path, request)
                if deck:
                    decks.append(deck)
                    self._save_deck(deck, pdf_output_path, pdf_path.stem)

            return decks
        finally:
            self._cleanup_notebooks()

    def _find_all_pdfs(
        self, input_path: Path, request: GenerateFlashcardsRequest
    ) -> list[Path]:
        """Find all PDF files recursively with filtering."""
        import fnmatch

        # Handle explicit file list first
        if request.explicit_files:
            pdf_paths: list[Path] = []
            for file_name in request.explicit_files:
                file_path = input_path / file_name
                if file_path.exists() and file_path.is_file():
                    pdf_paths.append(file_path)
                else:
                    logger.warning(f"Explicit file not found: {file_name}")
            return pdf_paths

        all_pdfs = [
            pdf_path
            for pdf_path in input_path.rglob("*.pdf")
            if self._is_safe_file_path(pdf_path, input_path)
        ]
        all_pptx = [
            pptx_path
            for pptx_path in input_path.rglob("*.pptx")
            if self._is_safe_file_path(pptx_path, input_path)
        ]
        all_files = all_pdfs + all_pptx

        if request.include_pattern:
            all_files = [
                f for f in all_files if fnmatch.fnmatch(f.name, request.include_pattern)
            ]
            logger.info(f"Include filter '{request.include_pattern}' applied")

        if request.exclude_pattern:
            all_files = [
                f
                for f in all_files
                if not fnmatch.fnmatch(f.name, request.exclude_pattern)
            ]
            logger.info(f"Exclude filter '{request.exclude_pattern}' applied")

        return all_files

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

            # Ensure resolved path is within allowed directory
            try:
                resolved_file.relative_to(resolved_input)
            except ValueError:
                logger.warning(f"Skipping file outside input directory: {file_path}")
                return False

            # Verify it's a regular file
            if not resolved_file.is_file():
                logger.warning(f"Skipping non-file path: {file_path}")
                return False

            # Verify it has a supported extension
            ext = resolved_file.suffix.lower()
            if ext not in self.SUPPORTED_EXTENSIONS:
                logger.warning(f"Skipping unsupported file type: {file_path}")
                return False

            # Check for empty files
            if resolved_file.stat().st_size == 0:
                logger.warning(f"Skipping empty file: {file_path}")
                return False

            return True
        except (OSError, ValueError) as e:
            logger.warning(f"Skipping invalid file path {file_path}: {e}")
            return False

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str:
        """Generate deck name from PDF path."""
        relative_path = pdf_path.relative_to(input_path)
        name_parts = [*list(relative_path.parent.parts), relative_path.stem]
        return "_".join(name_parts)

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        relative_path = pdf_path.relative_to(input_path)
        parent = relative_path.parent
        subdir = output_path if parent == Path(".") else output_path / parent
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir

    def _cleanup_notebooks(self) -> None:
        """Clean up created notebooks."""
        if not self._created_notebooks:
            return

        logger.info(f"Cleaning up {len(self._created_notebooks)} notebook(s)...")
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
        notebook_id = self.generator.create_notebook(f"Flashcards: {deck_name}")
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
    ) -> Deck | None:
        """Process large PDF by splitting into chunks.

        Each chunk is processed independently in its own notebook, then all
        flashcards are combined into a single deck.
        """
        temp_dir = pdf_output_path / ".temp_chunks"
        chunks: list[Path] = []
        all_flashcards = []

        try:
            chunks = list(self.pdf_chunker.chunk_pdf(pdf_path, temp_dir))
            logger.info(f"Processing {len(chunks)} chunks independently...")

            for i, chunk_path in enumerate(chunks, 1):
                logger.info(f"Processing chunk {i}/{len(chunks)}...")
                chunk_deck = self._process_chunk(
                    chunk_path, deck_name, pdf_output_path, request, i, len(chunks)
                )
                if chunk_deck and chunk_deck.flashcards:
                    all_flashcards.extend(chunk_deck.flashcards)
                    logger.info(
                        f"Chunk {i}/{len(chunks)}: "
                        f"{len(chunk_deck.flashcards)} flashcards"
                    )
                else:
                    logger.warning(f"Chunk {i}/{len(chunks)}: no flashcards generated")

                if i < len(chunks):
                    logger.debug(f"Waiting {CHUNK_DELAY_SECONDS}s before next chunk...")
                    time.sleep(CHUNK_DELAY_SECONDS)

            if not all_flashcards:
                logger.error("No flashcards generated from any chunk")
                return None

            logger.info(f"Total flashcards from all chunks: {len(all_flashcards)}")

            # Create combined deck with all flashcards
            combined_deck = Deck(
                name=deck_name,
                description=f"Deck de {deck_name} ({len(chunks)} chunks)",
                flashcards=all_flashcards,
                notebook_id="",  # No single notebook - chunks had separate notebooks
            )

            removed = combined_deck.deduplicate(similarity_threshold=0.85)
            if removed > 0:
                logger.info(f"Removed {removed} duplicate flashcards")

            # Apply quality filter
            self._apply_quality_filter(combined_deck)

            return combined_deck

        finally:
            self.pdf_chunker.cleanup_chunks(chunks)

    def _apply_quality_filter(self, deck: Deck) -> None:
        """Apply quality filtering to remove trivial and similar cards."""
        if not deck.flashcards:
            return

        quality_filter = QualityFilter()
        cards_to_keep = []
        trivial_count = 0

        for card in deck.flashcards:
            if quality_filter.is_trivial(card.front, card.back):
                trivial_count += 1
            else:
                cards_to_keep.append(card)

        if trivial_count > 0:
            logger.info(f"Quality filter removed {trivial_count} trivial cards")

        # Find and remove similar cards
        if len(cards_to_keep) > 1:
            card_tuples = [(c.front, c.back) for c in cards_to_keep]
            similar_pairs = quality_filter.find_similar_cards(card_tuples)

            indices_to_remove = {j for _, j, _ in similar_pairs}
            filtered_cards = [
                c for idx, c in enumerate(cards_to_keep) if idx not in indices_to_remove
            ]

            removed_similar = len(cards_to_keep) - len(filtered_cards)
            if removed_similar > 0:
                logger.info(f"Quality filter removed {removed_similar} similar cards")

            cards_to_keep = filtered_cards

        deck.flashcards = cards_to_keep

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
            chunk_path, deck_name, pdf_output_path, request, chunk_index, total_chunks
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
        delay = CHUNK_RETRY_INITIAL_DELAY

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
                if result is not None:
                    return result

                if attempt < CHUNK_RETRY_MAX_ATTEMPTS:
                    logger.warning(
                        f"Chunk {chunk_index}/{total_chunks}: "
                        f"retry {attempt}/{CHUNK_RETRY_MAX_ATTEMPTS} in {delay}s..."
                    )
                    time.sleep(delay)
                    delay = min(
                        delay * CHUNK_RETRY_BACKOFF_MULTIPLIER, CHUNK_RETRY_MAX_DELAY
                    )

            except RuntimeError as e:
                error_msg = str(e).lower()
                is_rate_limit = any(
                    pattern in error_msg
                    for pattern in [
                        "rpc create_artifact",
                        "rate limit",
                        "generation_failed",
                    ]
                )

                if is_rate_limit and attempt < CHUNK_RETRY_MAX_ATTEMPTS:
                    logger.warning(
                        f"Chunk {chunk_index}/{total_chunks}: "
                        f"rate limit error, retry {attempt}/{CHUNK_RETRY_MAX_ATTEMPTS} "
                        f"in {delay}s..."
                    )
                    time.sleep(delay)
                    delay = min(
                        delay * CHUNK_RETRY_BACKOFF_MULTIPLIER, CHUNK_RETRY_MAX_DELAY
                    )
                else:
                    logger.error(f"Chunk {chunk_index}/{total_chunks}: {e}")
                    return None

        return None

    def _process_chunk_internal(
        self,
        chunk_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        chunk_index: int,
        total_chunks: int,
    ) -> Deck | None:
        chunk_notebook_id = None
        try:
            chunk_deck_name = f"{deck_name}_chunk{chunk_index}"
            chunk_notebook_id = self._create_notebook(chunk_deck_name)

            source_id = self._add_pdf_source(chunk_notebook_id, chunk_path)
            if not source_id:
                logger.error(f"Failed to add chunk {chunk_index} as source")
                return None

            logger.info(f"Chunk {chunk_index}/{total_chunks}: source added, waiting...")
            self.generator.wait_for_source(
                chunk_notebook_id, source_id, timeout=SOURCE_WAIT_TIMEOUT
            )

            logger.info(f"Chunk {chunk_index}/{total_chunks}: generating flashcards...")
            instructions = request.instructions or self.DEFAULT_INSTRUCTIONS
            chunk_instructions = (
                f"{instructions}\n\n"
                f"CONTEXT: This is part {chunk_index} of {total_chunks} "
                f"of the document."
            )

            gen_config = GenerationConfig(
                difficulty=request.difficulty,
                quantity=request.quantity,
                instructions=chunk_instructions,
                timeout_seconds=request.timeout,
                wait_for_completion=request.wait_for_completion,
            )

            artifact_id = self.generator.generate_flashcards(
                chunk_notebook_id, gen_config
            )
            if not artifact_id:
                logger.error(f"Chunk {chunk_index}/{total_chunks}: failed to generate")
                return None

            completed = self.generator.wait_for_artifact(
                chunk_notebook_id, artifact_id, timeout=request.timeout
            )

            if not completed:
                logger.warning(f"Chunk {chunk_index}/{total_chunks}: timeout")
                return None

            temp_chunk_name = f"chunk{chunk_index}"
            json_path = pdf_output_path / _safe_filename(temp_chunk_name, "_raw.json")
            try:
                self.generator.download_flashcards(
                    chunk_notebook_id, artifact_id, json_path
                )
                flashcards = self.generator.parse_flashcards(json_path)
            finally:
                try:
                    json_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning(f"Failed to cleanup temp file: {e}")

            cloze_cards = []
            for card in flashcards:
                cloze_card = self.converter.convert(card)
                if cloze_card:
                    cloze_card.tags.append(deck_name.lower().replace(" ", "_"))
                    cloze_cards.append(cloze_card)

            logger.info(f"Chunk {chunk_index}/{total_chunks}: {len(cloze_cards)} cards")

            return Deck(
                name=chunk_deck_name,
                description=f"Chunk {chunk_index} of {total_chunks}",
                flashcards=cloze_cards,
                notebook_id=chunk_notebook_id,
            )

        except (GenerationError, SourceProcessingError, OSError, RuntimeError) as e:
            logger.error(f"Chunk {chunk_index}/{total_chunks}: error - {e}")
            raise
        finally:
            if chunk_notebook_id and chunk_notebook_id in self._created_notebooks:
                try:
                    self.generator.delete_notebook(chunk_notebook_id)
                    self._created_notebooks.remove(chunk_notebook_id)
                    logger.debug(
                        f"Chunk {chunk_index}/{total_chunks}: notebook cleaned up"
                    )
                except NotebookCleanupError as e:
                    logger.warning(f"Failed to cleanup chunk notebook: {e}")

    def _process_pdf(
        self,
        pdf_path: Path,
        input_path: Path,
        output_path: Path,
        request: GenerateFlashcardsRequest,
    ) -> Deck | None:
        """Process single PDF file."""
        deck_name = self._get_deck_name(pdf_path, input_path)
        pdf_output_path = self._get_output_subdir(pdf_path, input_path, output_path)

        filename = pdf_path.stem
        expected_csv = pdf_output_path / _safe_filename(filename, ".csv")
        if expected_csv.exists():
            logger.info(f"Skipping {pdf_path.name} - CSV already exists")
            return None

        logger.info("=" * BORDER_LENGTH)
        logger.info(f"PDF: {pdf_path.relative_to(input_path)}")
        logger.info(f"Deck: {deck_name}")
        logger.info("=" * BORDER_LENGTH)

        try:
            if pdf_path.suffix.lower() == ".pdf" and self.pdf_chunker.needs_chunking(
                pdf_path, threshold=PDF_CHUNKING_THRESHOLD
            ):
                logger.info(
                    f"Large PDF detected (>{PDF_CHUNKING_THRESHOLD} pages), "
                    "using chunking..."
                )
                return self._process_large_pdf(
                    pdf_path, deck_name, pdf_output_path, request
                )

            notebook_id = self._create_notebook(deck_name)
            source_id = self._add_pdf_source(notebook_id, pdf_path)
            if not source_id:
                return None
            logger.info("Processing source...")
            self.generator.wait_for_source(
                notebook_id, source_id, timeout=SOURCE_WAIT_TIMEOUT
            )

            return self._generate_flashcards(
                notebook_id, deck_name, pdf_output_path, request, pdf_path.stem
            )

        except GenerationError as e:
            logger.error(f"Generation error: {e}")
            return None
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(f"Processing error: {e}")
            return None
        except Exception as e:
            # Catch-all for unexpected errors during PDF processing
            logger.error(f"Unexpected error processing PDF: {e}")
            return None

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
        artifact_id = self.generator.generate_flashcards(notebook_id, gen_config)

        if not artifact_id:
            logger.error("Failed to generate flashcards")
            return None

        return self._handle_artifact_completion(
            notebook_id, artifact_id, pdf_output_path, deck_name, request, pdf_stem
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

        try:
            self.generator.download_flashcards(notebook_id, artifact_id, json_path)
            flashcards = self.generator.parse_flashcards(json_path)
        finally:
            try:
                json_path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"Failed to cleanup temp file: {e}")

        cloze_cards = []
        for card in flashcards:
            cloze_card = self.converter.convert(card)
            if cloze_card:
                cloze_card.tags.append(deck_name.lower().replace(" ", "_"))
                cloze_cards.append(cloze_card)

        deck = Deck(
            name=deck_name,
            description=f"Deck de {deck_name}",
            flashcards=cloze_cards,
            notebook_id=notebook_id,
        )

        try:
            self.generator.delete_notebook(notebook_id)
            if notebook_id in self._created_notebooks:
                self._created_notebooks.remove(notebook_id)
            logger.info("Notebook deleted")
        except NotebookCleanupError:
            pass

        return deck

    def _save_deck(self, deck: Deck, output_path: Path, pdf_stem: str) -> None:
        """Save deck to output directory."""
        csv_path = output_path / _safe_filename(pdf_stem, ".csv")
        self.exporter.export_csv(deck, csv_path)
        logger.info(f"Saved to: {csv_path}")
