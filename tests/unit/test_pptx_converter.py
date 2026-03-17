"""Tests for PPTX converter functionality."""

from unittest.mock import MagicMock, patch

from flashcards_generator.infrastructure.pdf_utils import PPTXConverter


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

            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=5)
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
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            # Create expected PDF file
            expected_pdf = output_dir / "test.pdf"

            def mock_run_func(*args, **kwargs):
                # Simulate LibreOffice creating the PDF file
                output_dir.mkdir(parents=True, exist_ok=True)
                expected_pdf.write_text("PDF content")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = mock_run_func

            result = converter.convert(pptx_path, output_dir)

            assert result is not None
            assert result.name == "test.pdf"

    def test_convert_libreoffice_fails(self, tmp_path):
        """Test conversion when LibreOffice returns error."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="Conversion failed")
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_pdf_not_created(self, tmp_path):
        """Test conversion when PDF file is not created."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Don't create the PDF file - simulate failure
            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_timeout(self, tmp_path):
        """Test conversion timeout handling."""
        with patch("subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired(cmd="soffice", timeout=120)
            converter = PPTXConverter()
            converter._has_libreoffice = True

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None

    def test_convert_oserror(self, tmp_path):
        """Test conversion OSError handling."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            converter = PPTXConverter()
            converter._has_libreoffice = True

            # Now apply OSError for the convert call
            mock_run.side_effect = OSError("Permission denied")

            pptx_path = tmp_path / "test.pptx"
            pptx_path.write_text("dummy content")
            output_dir = tmp_path / "output"

            result = converter.convert(pptx_path, output_dir)

            assert result is None
