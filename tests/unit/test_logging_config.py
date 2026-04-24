"""Tests for logging configuration."""

import logging

from flashcards_generator.infrastructure.logging_config import configure_logging


def test_configure_logging_reduces_pypdf_noise():
    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level

    try:
        configure_logging("INFO")
        assert pypdf_logger.level == logging.ERROR
    finally:
        pypdf_logger.setLevel(previous_level)
