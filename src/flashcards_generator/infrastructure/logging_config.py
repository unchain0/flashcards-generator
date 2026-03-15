import sys

from loguru import logger


def configure_logging(level: str = "INFO") -> None:
    logger.remove()

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


def get_logger(name: str):
    return logger.bind(name=name)
