"""Tests for __main__.py entry point."""

import runpy
import sys
from unittest.mock import patch


class TestMainEntryPoint:
    """Test python -m flashcards_generator entry point."""

    def test_main_import(self):
        """Test that __main__ can be imported without executing."""
        # Import should not raise
        from flashcards_generator import __main__

        # Verify it has the expected structure
        assert hasattr(__main__, "main")

    @patch("flashcards_generator.interfaces.main.main")
    def test_main_execution(self, mock_main):
        """Test that module execution calls the primary dispatcher."""
        sys.modules.pop("flashcards_generator.__main__", None)
        runpy.run_module("flashcards_generator.__main__", run_name="__main__")

        mock_main.assert_called_once_with()
