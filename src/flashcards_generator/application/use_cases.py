
import contextlib
from typing import TYPE_CHECKING

from flashcards_generator.application.converter import ClozeConverter
from flashcards_generator.application.exporter import DeckExporter
from flashcards_generator.domain.entities import Deck
from flashcards_generator.infrastructure.logging_config import get_logger
from flashcards_generator.infrastructure.notebooklm_client import NotebookLMClient
from flashcards_generator.infrastructure.paths import find_notebooklm

if TYPE_CHECKING:
    from pathlib import Path

    from flashcards_generator.domain.value_objects import Config

logger = get_logger("use_cases")


class GenerateFlashcardsUseCase:
    def __init__(self, config: Config):
        self.config = config
        self.client = NotebookLMClient(find_notebooklm(), config.timeout)
        self.converter = ClozeConverter()
        self.exporter = DeckExporter()
        self.created_notebooks: list[str] = []

    def _cleanup_notebooks(self):
        if self.created_notebooks:
            logger.info(f"Limpando {len(self.created_notebooks)} notebook(s)...")
            for notebook_id in self.created_notebooks:
                try:
                    self.client.delete_notebook(notebook_id)
                    logger.info(f"Deletado: {notebook_id[:8]}...")
                except Exception:
                    pass
            self.created_notebooks.clear()

    def execute(self) -> list[Deck]:
        input_path = self.config.input_dir
        output_path = self.config.output_dir
        output_path.mkdir(parents=True, exist_ok=True)

        decks = []
        try:
            all_pdfs = self._find_all_pdfs(input_path)

            if not all_pdfs:
                logger.warning(f"Nenhum PDF encontrado em {input_path}")
                return decks

            logger.info(f"{len(all_pdfs)} PDF(s) encontrado(s)")

            for pdf_path in sorted(all_pdfs):
                deck = self._process_pdf(pdf_path, input_path, output_path)
                if deck:
                    decks.append(deck)
                    self._save_deck(deck, output_path)

            return decks
        finally:
            self._cleanup_notebooks()

    def _find_all_pdfs(self, input_path: Path) -> list[Path]:
        return list(input_path.rglob("*.pdf"))

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str:
        relative_path = pdf_path.relative_to(input_path)
        name_parts = [*list(relative_path.parent.parts), relative_path.stem]
        return "_".join(name_parts)

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        relative_path = pdf_path.relative_to(input_path)
        if relative_path.parent:
            subdir = output_path / relative_path.parent
            subdir.mkdir(parents=True, exist_ok=True)
            return subdir
        return output_path

    def _get_default_instructions(self) -> str:
        return (
            "Gere flashcards de alta qualidade para estudo com spaced repetition. "
            "FORMATO PRIMÁRIO (90% dos cards): Use Cloze Deletion. "
            "Crie frases completas onde o conceito importante está envolto em {{c1::conceito}}. "
            "Exemplo de formato correto: 'Um {{c1::conjunto}} é uma coleção de elementos.' "
            "REGRAS DE QUALIDADE: "
            "1. Teste apenas CONCEITOS IMPORTANTES: definições, termos técnicos, formulas, notações matemáticas. "
            "2. NUNCA crie cloze para palavras triviais como: é, são, de, da, um, uma, o, a, em. "
            "3. Frases devem ser curtas (máximo 20-30 palavras) e diretas. "
            "4. Cada card deve testar UMA ÚNICA ideia central. "
            r"5. Use LaTeX $...$ para matemática: $x \in S$, $\emptyset$, $|S|$. "
            "6. Priorize: definições formais, teoremas, propriedades, exemplos concretos. "
            "7. Evite: perguntas que possam ser respondidas por uma única palavra trivial, "
            "   traduções simples, ou perguntas óbvias. "
            "FORMATO DOS DADOS: Frente (com cloze);Verso (resposta completa)"
        )

    def _create_notebook(self, deck_name: str) -> str:
        notebook_id = self.client.create_notebook(f"Flashcards: {deck_name}")
        self.created_notebooks.append(notebook_id)
        return notebook_id

    def _add_pdf_source(self, notebook_id: str, pdf_path: Path) -> str | None:
        try:
            source_id = self.client.add_source(notebook_id, pdf_path)
            logger.info(f"Fonte adicionada: {source_id[:8]}...")
            return source_id
        except Exception as e:
            logger.error(f"Erro ao adicionar PDF: {e}")
            logger.info(f"Notebook preservado: {notebook_id}")
            return None

    def _generate_flashcards(
        self, notebook_id: str, deck_name: str, pdf_output_path: Path
    ) -> Deck | None:
        instructions = self.config.instructions or self._get_default_instructions()

        logger.info("Gerando flashcards...")
        artifact_id = self.client.generate_flashcards(
            notebook_id=notebook_id,
            difficulty=self.config.difficulty,
            quantity=self.config.quantity,
            instructions=instructions,
        )

        if not artifact_id:
            logger.error("Falha ao gerar flashcards")
            return None

        return self._handle_artifact_completion(
            notebook_id, artifact_id, pdf_output_path, deck_name
        )

    def _handle_artifact_completion(
        self, notebook_id: str, artifact_id: str, output_path: Path, deck_name: str
    ) -> Deck:
        logger.info("Aguardando geração...")

        if not self.config.wait_for_completion:
            logger.info(f"Geração em background. ID: {artifact_id}")
            return Deck(
                name=deck_name,
                description=f"Deck {deck_name} (gerando)",
                notebook_id=notebook_id,
            )

        if self.client.wait_for_artifact(
            notebook_id, artifact_id, timeout=self.config.timeout
        ):
            return self._download_and_convert(
                notebook_id, artifact_id, output_path, deck_name
            )

        logger.warning(f"Timeout. ID: {artifact_id}")
        logger.info(f"Notebook preservado para retry: {notebook_id}")
        return Deck(
            name=deck_name, description=f"Deck {deck_name}", notebook_id=notebook_id
        )

    def _process_pdf(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Deck | None:
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

            logger.info("Processando fonte...")
            self.client.wait_for_source(notebook_id, source_id, timeout=600)

            return self._generate_flashcards(notebook_id, deck_name, pdf_output_path)

        except Exception as e:
            logger.error(f"Erro: {e}")
            return None

    def _download_and_convert(
        self, notebook_id: str, artifact_id: str, output_path: Path, deck_name: str
    ) -> Deck:
        json_path = output_path / f"{deck_name}_raw.json"
        self.client.download_flashcards(notebook_id, artifact_id, json_path)

        flashcards = self.client.parse_flashcards(json_path)

        with contextlib.suppress(Exception):
            json_path.unlink()

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
            self.client.delete_notebook(notebook_id)
            if notebook_id in self.created_notebooks:
                self.created_notebooks.remove(notebook_id)
            logger.info("Notebook deletado")
        except Exception:
            pass

        return deck

    def _save_deck(self, deck: Deck, output_path: Path) -> None:
        base_path = output_path
        for part in deck.name.split("_")[:-1]:
            base_path = base_path / part
            base_path.mkdir(exist_ok=True)

        filename = deck.name.split("_")[-1]

        self.exporter.export_csv(deck, base_path / f"{filename}.csv")

        logger.info(f"Salvo em: {base_path}")
