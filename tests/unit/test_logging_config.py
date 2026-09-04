"""Tests for logging configuration."""

import io
import logging

from loguru import logger

from flashcards_generator.infrastructure import logging_config
from flashcards_generator.infrastructure.logging_config import (
    configure_logging,
    get_logger,
)


def test_configure_logging_reduces_pypdf_noise():
    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level

    try:
        configure_logging("INFO")
        assert pypdf_logger.level == logging.ERROR
    finally:
        pypdf_logger.setLevel(previous_level)


def test_configured_component_is_rendered_without_ansi_on_redirected_stderr(
    monkeypatch,
):
    redirected_stderr = io.StringIO()
    monkeypatch.setattr(logging_config.sys, "stderr", redirected_stderr)

    configure_logging("INFO")
    get_logger("sentinel_component").info("captured message")
    logger.complete()

    output = redirected_stderr.getvalue()
    assert "sentinel_component" in output
    assert "\x1b[" not in output
