import argparse
import subprocess
import sys
from pathlib import Path

from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase
from flashcards_generator.domain.value_objects import Config
from flashcards_generator.infrastructure.logging_config import (
    configure_logging,
    get_logger,
)
from flashcards_generator.infrastructure.paths import find_notebooklm

logger = get_logger("cli")


class CLI:
    def __init__(self):
        self.parser = self._create_parser()

    def _create_parser(self) -> argparse.ArgumentParser:
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
            "--difficulty", "-d", choices=["easy", "medium", "hard"], default="medium"
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
            "--no-wait", action="store_true", help="Não aguarda conclusão"
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
        notebooklm = find_notebooklm()
        try:
            result = subprocess.run(
                [notebooklm, "auth", "check"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 and "✓" in result.stdout
        except Exception:
            return False

    def _validate_input(self, input_dir: Path) -> bool:
        if not input_dir.exists():
            logger.error(f"Diretório não existe: {input_dir}")
            return False
        return True

    def _authenticate(self, skip_auth_check: bool) -> bool:
        if skip_auth_check:
            return True
        logger.info("Verificando autenticação...")
        if not self.check_auth():
            logger.error("Não autenticado. Execute: notebooklm login")
            return False
        logger.info("Autenticado")
        return True

    def _set_language(self, language: str) -> None:
        notebooklm = find_notebooklm()
        try:
            subprocess.run(
                [notebooklm, "language", "set", language],
                capture_output=True,
                timeout=10,
            )
            logger.info(f"Idioma configurado: {language}")
        except Exception:
            logger.warning("Não foi possível configurar o idioma")

    def _create_config(self, args) -> Config:
        return Config(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            difficulty=args.difficulty,
            quantity=args.quantity,
            instructions=args.instructions or "",
            wait_for_completion=not args.no_wait,
            timeout=args.timeout,
        )

    def _log_config(self, config: Config) -> None:
        logger.info("Iniciando...")
        logger.info(f"Entrada: {config.input_dir}")
        logger.info(f"Saída: {config.output_dir}")
        logger.info(f"Dificuldade: {config.difficulty}")
        logger.info(f"Quantidade: {config.quantity}")

    def _print_summary(self, decks: list) -> None:
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
        args = self.parser.parse_args()
        configure_logging(args.log_level)

        if not self._validate_input(args.input_dir):
            return 1

        if not self._authenticate(args.skip_auth_check):
            return 1

        self._set_language(args.language)
        config = self._create_config(args)
        self._log_config(config)

        use_case = GenerateFlashcardsUseCase(config)
        decks = use_case.execute()

        self._print_summary(decks)
        return 0


def main():
    cli = CLI()
    sys.exit(cli.run())
