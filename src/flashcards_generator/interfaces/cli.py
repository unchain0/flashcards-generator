"""CLI interface with proper dependency wiring."""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter
from flashcards_generator.application.csv_merger import CsvMerger
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase
from flashcards_generator.domain.exceptions import CSVMergeError
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
        """Create argument parser with subcommands."""
        parser = argparse.ArgumentParser(
            description="Gera flashcards com Cloze Deletion a partir de PDFs"
        )
        parser.add_argument(
            "--log-level",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            default="INFO",
            help="Nível de log (padrão: INFO)",
        )

        subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

        # Generate command (default)
        generate_parser = subparsers.add_parser(
            "generate", help="Gerar flashcards de PDFs"
        )
        generate_parser.add_argument(
            "--input-dir",
            "-i",
            required=True,
            type=Path,
            help="Diretório com pastas de temas contendo PDFs",
        )
        generate_parser.add_argument(
            "--output-dir",
            "-o",
            type=Path,
            default=Path("./output"),
            help="Diretório raiz de saída; a estrutura relativa do input é preservada dentro dele",
        )
        generate_parser.add_argument(
            "--difficulty",
            "-d",
            choices=["easy", "medium", "hard"],
            default="medium",
        )
        generate_parser.add_argument(
            "--quantity",
            "-q",
            choices=["fewer", "standard", "more"],
            default="standard",
        )
        generate_parser.add_argument("--instructions", help="Instruções customizadas")
        generate_parser.add_argument(
            "--language",
            "-l",
            default="pt_BR",
            help="Idioma dos flashcards (padrão: pt_BR)",
        )
        generate_parser.add_argument(
            "--no-wait",
            action="store_true",
            help="Não aguarda conclusão",
        )
        generate_parser.add_argument("--timeout", type=int, default=900)
        generate_parser.add_argument("--skip-auth-check", action="store_true")
        generate_parser.add_argument(
            "--include",
            type=str,
            help="Padrão glob para incluir PDFs (ex: 'capitulo*.pdf')",
        )
        generate_parser.add_argument(
            "--exclude",
            type=str,
            help="Padrão glob para excluir PDFs (ex: '*_old.pdf')",
        )
        generate_parser.add_argument(
            "--files",
            type=str,
            help="Lista explícita de PDFs separados por vírgula",
        )

        # Cleanup command
        cleanup_parser = subparsers.add_parser(
            "cleanup", help="Limpar notebooks do NotebookLM"
        )
        cleanup_parser.add_argument(
            "--days",
            "-d",
            type=int,
            help=(
                "Deletar apenas notebooks criados nos últimos N dias "
                "(ex: 1=hoje, 2=ontem+hoje)"
            ),
        )
        cleanup_parser.add_argument(
            "--all",
            "-a",
            action="store_true",
            help="Deletar todos os notebooks",
        )
        cleanup_parser.add_argument("--skip-auth-check", action="store_true")

        merge_parser = subparsers.add_parser(
            "merge", help="Mesclar arquivos CSV de flashcards"
        )
        merge_parser.add_argument(
            "--folder",
            "-f",
            required=True,
            type=Path,
            help="Pasta contendo arquivos CSV para mesclar",
        )
        merge_parser.add_argument(
            "--output",
            "-o",
            type=str,
            default="merged_flashcards.csv",
            help="Nome do arquivo de saída (padrão: merged_flashcards.csv)",
        )
        merge_parser.add_argument(
            "--deduplicate",
            "-d",
            action="store_true",
            help="Remover flashcards duplicados durante a mescla",
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

    def _create_adapter(self, timeout: int = 900) -> NotebookLMAdapter:
        """Create NotebookLM adapter."""
        notebooklm_path = find_notebooklm()
        return NotebookLMAdapter(notebooklm_path, timeout=timeout)

    def _create_use_case(self, args: argparse.Namespace) -> GenerateFlashcardsUseCase:
        """Create use case with dependencies wired."""
        generator = self._create_adapter(args.timeout)
        return GenerateFlashcardsUseCase(generator=generator)

    def _create_request(self, args: argparse.Namespace) -> GenerateFlashcardsRequest:
        """Create request DTO from CLI args."""
        explicit_files = []
        if args.files:
            explicit_files = [f.strip() for f in args.files.split(",")]
        return GenerateFlashcardsRequest(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            difficulty=args.difficulty,
            quantity=args.quantity,
            instructions=args.instructions or "",
            wait_for_completion=not args.no_wait,
            timeout=args.timeout,
            include_pattern=args.include,
            exclude_pattern=args.exclude,
            explicit_files=explicit_files,
        )

    def _log_config(self, request: GenerateFlashcardsRequest) -> None:
        """Log configuration."""
        logger.info("Iniciando...")
        logger.info(f"Entrada: {request.input_dir}")
        logger.info(f"Saída: {request.output_dir} (preservando a estrutura relativa)")
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

    def _run_generate(self, args: argparse.Namespace) -> int:
        """Run generate command."""
        if not self._validate_input(args.input_dir):
            return 1

        if not self._authenticate(args.skip_auth_check):
            return 1

        self._set_language(args.language)

        use_case = self._create_use_case(args)
        request = self._create_request(args)
        self._log_config(request)

        try:
            decks = use_case.execute(request)
        except KeyboardInterrupt:
            logger.info("\n⚠️  Operation cancelled by user")
            return 130

        self._print_summary(decks)
        return 0

    def _run_cleanup(self, args: argparse.Namespace) -> int:
        """Run cleanup command."""
        if not self._authenticate(args.skip_auth_check):
            return 1

        adapter = self._create_adapter()

        if args.days:
            logger.info(f"Deletando notebooks dos últimos {args.days} dia(s)...")
            deleted, failed = adapter.delete_all_notebooks(
                days=args.days, show_progress=True
            )
        elif args.all:
            logger.info("Deletando todos os notebooks...")
            deleted, failed = adapter.delete_all_notebooks(show_progress=True)
        else:
            logger.error("Especifique --days ou --all")
            return 1

        if failed > 0:
            logger.warning(f"{failed} notebook(s) não puderam ser deletados")
            return 0 if deleted > 0 else 1

        logger.info(f"✅ {deleted} notebook(s) deletado(s) com sucesso")
        return 0

    def _run_merge(self, args: argparse.Namespace) -> int:
        """Run merge command."""
        if not args.folder.exists():
            logger.error(f"Pasta não existe: {args.folder}")
            return 1

        request = MergeCsvRequest(
            folder_path=args.folder,
            output_filename=args.output,
            deduplicate=args.deduplicate,
        )

        try:
            rows = CsvMerger.merge(request)
            output_path = args.folder / args.output
            logger.info(f"✅ {rows} flashcards mesclados em: {output_path}")
            return 0
        except CSVMergeError as e:
            logger.error(f"Erro ao mesclar: {e.reason}")
            return 1

    def run(self) -> int:
        """Run CLI."""
        args = self.parser.parse_args()
        configure_logging(args.log_level)

        if args.command == "cleanup":
            return self._run_cleanup(args)
        elif args.command == "merge":
            return self._run_merge(args)
        elif args.command == "generate":
            return self._run_generate(args)
        else:
            # Default to generate if no command specified
            # But we need input-dir for generate, so show help if missing
            if not hasattr(args, "input_dir") or args.input_dir is None:
                self.parser.print_help()
                return 1
            return self._run_generate(args)


def main() -> None:
    """Entry point."""
    cli = CLI()
    try:
        sys.exit(cli.run())
    except KeyboardInterrupt:
        logger.info("\n⚠️  Operation cancelled by user")
        sys.exit(130)
