"""Tests for PPTX converter functionality."""

import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flashcards_generator.integrations.pdf_utils import PPTXConverter


class TestPPTXConverter:
    """Test PowerPoint to PDF conversion."""

    def test_init_checks_libreoffice(self):
        """Test that initialization checks for LibreOffice."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            converter = PPTXConverter()
            assert converter._has_libreoffice is True

    def test_init_libreoffice_not_found(self):
        """Test initialization when LibreOffice is not found."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            converter = PPTXConverter()
            assert converter._has_libreoffice is False

    def test_init_libreoffice_timeout(self):
        """Test initialization when LibreOffice check times out."""
        with patch("subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="test", timeout=5
            )
            converter = PPTXConverter()
            assert converter._has_libreoffice is False

    def test_convert_without_libreoffice(self, tmp_path):
        """Test conversion fails gracefully when LibreOffice is not available."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            converter = PPTXConverter()

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_success(self, tmp_path):
        """Test successful PPTX to PDF conversion."""
        with patch.object(PPTXConverter, "_run_conversion") as mock_run:
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            def mock_run_func(command):
                # Simulate LibreOffice creating the PDF in its per-call directory.
                conversion_dir = Path(command[command.index("--outdir") + 1])
                (conversion_dir / "test.pdf").write_text("PDF content")
                return subprocess.CompletedProcess(command, 0, "", "")

            mock_run.side_effect = mock_run_func

            result = converter.convert(pptx_path, output_dir)

            assert result is not None
            assert result.name == "test.pdf"

    def test_convert_libreoffice_fails(self, tmp_path):
        """Test conversion when LibreOffice returns error."""
        with patch.object(
            PPTXConverter,
            "_run_conversion",
            return_value=subprocess.CompletedProcess(
                ["soffice"], 1, "", "Conversion failed"
            ),
        ):
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_pdf_not_created(self, tmp_path):
        """Test conversion when PDF file is not created."""
        with patch.object(
            PPTXConverter,
            "_run_conversion",
            return_value=subprocess.CompletedProcess(["soffice"], 0, "", ""),
        ):
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Don't create the PDF file - simulate failure
            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_rejects_stale_output(self, tmp_path):
        """A previous conversion must not satisfy a no-op conversion call."""
        with patch.object(
            PPTXConverter,
            "_run_conversion",
            return_value=subprocess.CompletedProcess(["soffice"], 0, "", ""),
        ):
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"
            output_dir.mkdir()
            stale_pdf = output_dir / "test.pdf"
            stale_pdf.write_text("stale PDF")

            assert converter.convert(pptx_path, output_dir) is None
            assert stale_pdf.read_text() == "stale PDF"

    def test_convert_timeout(self, tmp_path):
        """Test conversion timeout handling."""
        with patch.object(
            PPTXConverter,
            "_run_conversion",
            side_effect=subprocess.TimeoutExpired(cmd="soffice", timeout=120),
        ):
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_oserror(self, tmp_path):
        """Test conversion OSError handling."""
        with patch.object(
            PPTXConverter,
            "_run_conversion",
            side_effect=OSError("Permission denied"),
        ):
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_timeout_terminates_the_libreoffice_process_group(
        self, tmp_path
    ):
        """Timeouts must not leave LibreOffice descendants running."""
        converter = PPTXConverter.__new__(PPTXConverter)
        converter._has_libreoffice = True
        pptx_path = tmp_path / "test.pptx"
        pptx_path.write_text("dummy content")
        output_dir = tmp_path / "output"

        process = MagicMock()
        process.pid = 4321
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("soffice", 120),
            ("", ""),
        ]

        with (
            patch(
                "flashcards_generator.integrations.pptx_converter.subprocess.Popen",
                return_value=process,
            ) as popen,
            patch(
                "flashcards_generator.integrations.pptx_converter.subprocess.run",
                side_effect=AssertionError(
                    "conversion must use a process handle"
                ),
            ),
            patch(
                "flashcards_generator.integrations.pptx_converter.os.killpg"
            ) as killpg,
        ):
            result = converter.convert(pptx_path, output_dir)

        assert result is None
        popen.assert_called_once()
        assert popen.call_args.kwargs["start_new_session"] is True
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)

    def test_run_conversion_returns_the_reaped_process_result(self) -> None:
        converter = PPTXConverter.__new__(PPTXConverter)
        command = ["soffice", "--headless"]
        process = MagicMock()
        process.communicate.return_value = ("converted", "")
        process.returncode = 0

        with patch(
            "flashcards_generator.integrations.pptx_converter.subprocess.Popen",
            return_value=process,
        ) as popen:
            result = converter._run_conversion(command)

        assert result.args == command
        assert result.returncode == 0
        assert result.stdout == "converted"
        assert result.stderr == ""
        process.communicate.assert_called_once_with(timeout=120)
        assert popen.call_args.kwargs["start_new_session"] is True

    def test_stop_process_escalates_to_sigkill_after_sigterm_timeout(
        self,
    ) -> None:
        converter = PPTXConverter.__new__(PPTXConverter)
        process = MagicMock()
        process.pid = 4321
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("soffice", 5),
            ("", ""),
        ]

        with patch(
            "flashcards_generator.integrations.pptx_converter.os.killpg"
        ) as killpg:
            converter._stop_process(process)

        assert [call.args for call in killpg.call_args_list] == [
            (process.pid, signal.SIGTERM),
            (process.pid, signal.SIGKILL),
        ]
        assert process.communicate.call_count == 2

    @pytest.mark.parametrize(
        ("signal_number", "fallback"),
        [(signal.SIGTERM, "terminate"), (signal.SIGKILL, "kill")],
    )
    def test_signal_falls_back_to_process_leader_when_group_is_gone(
        self, signal_number: int, fallback: str
    ) -> None:
        converter = PPTXConverter.__new__(PPTXConverter)
        process = MagicMock()
        process.pid = 4321

        with patch(
            "flashcards_generator.integrations.pptx_converter.os.killpg",
            side_effect=ProcessLookupError,
        ):
            converter._signal_process(process, signal_number)

        getattr(process, fallback).assert_called_once_with()
