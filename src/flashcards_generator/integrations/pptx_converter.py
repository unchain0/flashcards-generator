from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from flashcards_generator.integrations.logging_config import get_logger

logger = get_logger("pdf_utils")


class PPTXConverter:
    """Converts PowerPoint (.pptx) files to PDF format."""

    PROCESS_CLEANUP_TIMEOUT = 5

    def __init__(self) -> None:
        self._has_libreoffice = self._check_libreoffice()

    def _check_libreoffice(self) -> bool:
        """Check if LibreOffice is available."""
        try:
            result = subprocess.run(
                ["soffice", "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired, FileNotFoundError:
            logger.warning("LibreOffice not found. PPTX conversion disabled.")
            return False

    def convert(self, pptx_path: Path, output_dir: Path) -> Path | None:
        """Convert PPTX to PDF using LibreOffice."""
        if not self._has_libreoffice:
            logger.error(
                f"Cannot convert {pptx_path.name}: LibreOffice not available"
            )
            return None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)

            pdf_name = pptx_path.stem + ".pdf"
            pdf_path = output_dir / pdf_name
            with tempfile.TemporaryDirectory(
                prefix=f".{pptx_path.stem}-", dir=output_dir
            ) as conversion_dir:
                converted_pdf_path = Path(conversion_dir) / pdf_name
                result = self._run_conversion([
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    conversion_dir,
                    str(pptx_path),
                ])

                if not self._conversion_output_is_valid(
                    result, converted_pdf_path
                ):
                    return None

                converted_pdf_path.replace(pdf_path)

            logger.info(f"Converted {pptx_path.name} → {pdf_name}")
            return pdf_path

        except subprocess.TimeoutExpired:
            logger.error(f"PPTX conversion timeout: {pptx_path.name}")
            return None
        except OSError as e:
            logger.error(f"PPTX conversion error: {e}")
            return None

    @staticmethod
    def _conversion_output_is_valid(
        result: subprocess.CompletedProcess[str], converted_pdf_path: Path
    ) -> bool:
        if result.returncode != 0:
            logger.error(f"PPTX conversion failed: {result.stderr[:500]}")
            return False
        if not converted_pdf_path.is_file():
            logger.error(
                f"PDF not found after conversion: {converted_pdf_path}"
            )
            return False
        return True

    def _run_conversion(
        self, command: list[str]
    ) -> subprocess.CompletedProcess[str]:
        """Run LibreOffice in an isolated process group."""
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=False,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=120)
        except KeyboardInterrupt, subprocess.TimeoutExpired:
            self._stop_process(process)
            raise

        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        """Stop LibreOffice and reap its process group."""
        self._signal_process(process, signal.SIGTERM)
        try:
            process.communicate(timeout=self.PROCESS_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._signal_process(process, signal.SIGKILL)
            process.communicate(timeout=self.PROCESS_CLEANUP_TIMEOUT)

    @staticmethod
    def _signal_process(
        process: subprocess.Popen[str], signal_number: int
    ) -> None:
        """Signal the isolated group, falling back to its leader."""
        pid = getattr(process, "pid", None)
        if os.name == "posix" and isinstance(pid, int):
            try:
                os.killpg(pid, signal_number)
                return
            except OSError, ProcessLookupError:
                pass
        if signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
