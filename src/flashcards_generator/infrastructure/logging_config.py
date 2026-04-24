"""Logging configuration for the application."""

import logging
import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger


def _configure_third_party_loggers() -> None:
    """Reduce noise from third-party libraries during normal execution."""
    logging.getLogger("pypdf").setLevel(logging.ERROR)


def configure_logging(level: str = "INFO") -> None:
    """Configure application logging with loguru."""
    logger.remove()
    _configure_third_party_loggers()

    fmt = (
        "<green>{time:HH:mm:ss}</green> "
        "<level>{level:<8}</level> "
        "<cyan>{name}:{line}</cyan> - "
        "{message}"
    )

    logger.add(
        sys.stderr,
        level=level,
        format=fmt,
        colorize=True,
        enqueue=True,
    )


def get_logger(name: str) -> Logger:
    """Get a logger instance with the given name."""
    return logger.bind(name=name)
