"""CLI interface with proper dependency wiring."""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase
from flashcards_generator.infrastructure.logging_config import (
    configure_logging,
    get_logger,
)
from flashcards_generator.infrastructure.paths import find_notebooklm

if TYPE_CHECKING:
    from flashcards_generator.domain.entities import Deck

logger = get_logger("cli")


class CLI:
    """Command-line interface for flashcards generator."""

    def __init__(self) -> None:
        """Initialize CLI with argument parser."""
        self.parser = self._create_parser()

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create argument parser."""
        parser = argparse.ArgumentParser(
            description="Gera flashcards com Cloze Deletion a partir de PDFs"
        )
        parser.add_argument(
            "--input-dir",
            "-i",
            required=True,
            type=Path,
            help="Diretório com pastas de temas contendo PDFs",
        )
        parser.add_argument(
            "--output-dir",
            "-o",
            type=Path,
            default=Path("./output"),
            help="Diretório de saída",
        )
        parser.add_argument(
            "--difficulty",
            "-d",
            choices=["easy", "medium", "hard"],
            default="medium",
        )
        parser.add_argument(
            "--quantity",
            "-q",
            choices=["fewer", "standard", "more"],
            default="standard",
        )
        parser.add_argument("--instructions", help="Instruções customizadas")
        parser.add_argument(
            "--language",
            "-l",
            default="pt_BR",
            help="Idioma dos flashcards (padrão: pt_BR)",
        )
        parser.add_argument(
            "--no-wait",
            action="store_true",
            help="Não aguarda conclusão",
        )
        parser.add_argument("--timeout", type=int, default=900)
        parser.add_argument("--skip-auth-check", action="store_true")
        parser.add_argument(
            "--log-level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default="INFO",
            help="Nível de log (padrão: INFO)",
        )
        return parser

    def check_auth(self) -> bool:
        """Check if user is authenticated with NotebookLM."""
        notebooklm = find_notebooklm()
        try:
            result = subprocess.run(
                [notebooklm, "auth", "check"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 and "✓" in result.stdout
        except subprocess.TimeoutExpired, FileNotFoundError:
            return False

    def _validate_input(self, input_dir: Path) -> bool:
        """Validate input directory exists."""
        if not input_dir.exists():
            logger.error(f"Diretório não existe: {input_dir}")
            return False
        return True

    def _authenticate(self, skip_auth_check: bool) -> bool:
        """Verify authentication."""
        if skip_auth_check:
            return True
        logger.info("Verificando autenticação...")
        if not self.check_auth():
            logger.error("Não autenticado. Execute: notebooklm login")
            return False
        logger.info("Autenticado")
        return True

    def _set_language(self, language: str) -> None:
        """Set language for NotebookLM."""
        notebooklm = find_notebooklm()
        try:
            subprocess.run(
                [notebooklm, "language", "set", language],
                capture_output=True,
                timeout=10,
            )
            logger.info(f"Idioma configurado: {language}")
        except subprocess.TimeoutExpired, FileNotFoundError:
            logger.warning("Não foi possível configurar o idioma")

    def _create_use_case(self, args: argparse.Namespace) -> GenerateFlashcardsUseCase:
        """Create use case with dependencies wired."""
        notebooklm_path = find_notebooklm()

        # Create adapter (infrastructure concern)
        generator = NotebookLMAdapter(notebooklm_path, timeout=args.timeout)

        # Create use case with injected dependencies
        return GenerateFlashcardsUseCase(generator=generator)

    def _create_request(self, args: argparse.Namespace) -> GenerateFlashcardsRequest:
        """Create request DTO from CLI args."""
        return GenerateFlashcardsRequest(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            difficulty=args.difficulty,
            quantity=args.quantity,
            instructions=args.instructions or "",
            wait_for_completion=not args.no_wait,
            timeout=args.timeout,
        )

    def _log_config(self, request: GenerateFlashcardsRequest) -> None:
        """Log configuration."""
        logger.info("Iniciando...")
        logger.info(f"Entrada: {request.input_dir}")
        logger.info(f"Saída: {request.output_dir}")
        logger.info(f"Dificuldade: {request.difficulty}")
        logger.info(f"Quantidade: {request.quantity}")

    def _print_summary(self, decks: list[Deck]) -> None:
        """Print execution summary."""
        total_cards = sum(d.total_cards for d in decks)
        logger.info("=" * 60)
        logger.info("RESUMO")
        logger.info("=" * 60)
        logger.info(f"Temas: {len(decks)}")
        logger.info(f"Total de cards: {total_cards}")
        logger.info("Decks:")
        for deck in decks:
            status = "✅" if deck.flashcards else "⏳"
            logger.info(f"  {status} {deck.name}: {deck.total_cards} cards")

    def run(self) -> int:
        """Run CLI."""
        args = self.parser.parse_args()
        configure_logging(args.log_level)

        if not self._validate_input(args.input_dir):
            return 1

        if not self._authenticate(args.skip_auth_check):
            return 1

        self._set_language(args.language)

        # Create use case with wired dependencies
        use_case = self._create_use_case(args)
        request = self._create_request(args)
        self._log_config(request)

        decks = use_case.execute(request)

        self._print_summary(decks)
        return 0


def main() -> None:
    """Entry point."""
    cli = CLI()
    sys.exit(cli.run())
