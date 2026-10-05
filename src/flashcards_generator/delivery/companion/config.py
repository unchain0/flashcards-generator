from __future__ import annotations

from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from platformdirs import user_data_path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

COMPANION_PORT = 8766
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _validated_origin(value: str) -> tuple[SplitResult, str]:
    parsed = urlsplit(value.strip())
    if not all((
        parsed.scheme in {"http", "https"},
        parsed.username is None,
        parsed.password is None,
        parsed.path in {"", "/"},
        not parsed.query,
        not parsed.fragment,
    )):
        raise ValueError("web_origin must be an exact HTTP(S) origin")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("web_origin must be an exact HTTP(S) origin")
    if parsed.scheme == "http" and hostname not in _LOCAL_HTTP_HOSTS:
        raise ValueError("HTTP is allowed only for a local development origin")
    return parsed, hostname


def _format_origin(parsed: SplitResult, hostname: str) -> str:
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    hostname = hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = (
        hostname if port in {None, default_port} else f"{hostname}:{port}"
    )
    return f"{parsed.scheme.lower()}://{authority}"


class CompanionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLASHCARDS_COMPANION_",
        extra="ignore",
    )

    web_origin: str
    data_dir: Path = Field(
        default_factory=lambda: (
            Path(user_data_path("flashcards-generator", appauthor=False))
            / "companion"
        )
    )

    @field_validator("web_origin")
    @classmethod
    def normalize_web_origin(cls, value: str) -> str:
        parsed, hostname = _validated_origin(value)
        return _format_origin(parsed, hostname)
