"""Generate flashcards use case with dependency injection."""

from pathlib import Path

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

# Explicit runtime usage to prevent type-checking-only false positives
_ = (Path, GenerateFlashcardsRequest)

logger = get_logger("use_cases")


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
        "REGRAS: "
        "1. Teste apenas CONCEITOS IMPORTANTES: definições, termos técnicos, "
        "fórmulas, notações matemáticas. "
        "2. NUNCA crie cloze para palavras triviais como: é, são, de, da, um, uma. "
        "3. Frases curtas (máximo 20-30 palavras) e diretas. "
        "4. Cada card deve testar UMA ÚNICA ideia central. "
        "5. Use LaTeX $...$ para matemática. "
        "6. Priorize: definições, teoremas, propriedades, exemplos. "
        "7. Evite: perguntas triviais, traduções simples ou óbvias. "
        "8. NÃO gere flashcards no formato pergunta-resposta, apenas cloze. "
        "9. O VERSO deve conter detalhes extras/explicação, "
        "NÃO apenas a resposta. "
        "FORMATO: Frente (cloze com resposta oculta); "
        "Verso (detalhes/explicação adicional)"
    )

    def __init__(
        self,
        generator: FlashcardGeneratorPort,
        converter: ClozeConverter | None = None,
        exporter: DeckExporter | None = None,
    ):
        self.generator = generator
        self.converter = converter or ClozeConverter()
        self.exporter = exporter or DeckExporter()
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

        decks: list[Deck] = []
        try:
            all_pdfs = self._find_all_pdfs(input_path)

            if not all_pdfs:
                logger.warning(f"No PDFs found in {input_path}")
                return decks

            logger.info(f"{len(all_pdfs)} PDF(s) found")

            for pdf_path in sorted(all_pdfs):
                deck = self._process_pdf(pdf_path, input_path, output_path, request)
                if deck:
                    decks.append(deck)
                    self._save_deck(deck, output_path)

            return decks
        finally:
            self._cleanup_notebooks()

    def _find_all_pdfs(self, input_path: Path) -> list[Path]:
        """Find all PDF files recursively."""
        return list(input_path.rglob("*.pdf"))

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str:
        """Generate deck name from PDF path."""
        relative_path = pdf_path.relative_to(input_path)
        name_parts = [*list(relative_path.parent.parts), relative_path.stem]
        return "_".join(name_parts)

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        """Get or create output subdirectory for PDF."""
        relative_path = pdf_path.relative_to(input_path)
        if relative_path.parent:
            subdir = output_path / relative_path.parent
            subdir.mkdir(parents=True, exist_ok=True)
            return subdir
        return output_path

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

        logger.info("=" * 60)
        logger.info(f"PDF: {pdf_path.relative_to(input_path)}")
        logger.info(f"Deck: {deck_name}")
        logger.info("=" * 60)

        try:
            notebook_id = self._create_notebook(deck_name)
            source_id = self._add_pdf_source(notebook_id, pdf_path)

            if not source_id:
                return None

            logger.info("Processing source...")
            self.generator.wait_for_source(notebook_id, source_id, timeout=600)

            return self._generate_flashcards(
                notebook_id, deck_name, pdf_output_path, request
            )

        except GenerationError as e:
            logger.error(f"Generation error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return None

    def _generate_flashcards(
        self,
        notebook_id: str,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
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
            notebook_id, artifact_id, pdf_output_path, deck_name, request
        )

    def _handle_artifact_completion(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        request: GenerateFlashcardsRequest,
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
                notebook_id, artifact_id, output_path, deck_name
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
    ) -> Deck:
        """Download and convert flashcards."""
        json_path = output_path / f"{deck_name}_raw.json"

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

    def _save_deck(self, deck: Deck, output_path: Path) -> None:
        """Save deck to output directory."""
        base_path = output_path
        for part in deck.name.split("_")[:-1]:
            base_path = base_path / part
            base_path.mkdir(exist_ok=True)

        filename = deck.name.split("_")[-1]
        self.exporter.export_csv(deck, base_path / f"{filename}.csv")
        logger.info(f"Saved to: {base_path}")
