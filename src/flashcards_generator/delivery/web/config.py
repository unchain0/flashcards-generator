from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FLASHCARDS_",
        extra="ignore",
    )

    app_name: str = "Flashcards Generator"
    environment: Environment = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite+aiosqlite:///./flashcards.db"
    data_dir: Path = Path("./data")
    session_secret: SecretStr = SecretStr(token_urlsafe(32))
    auth_lookup_secret: SecretStr = SecretStr(token_urlsafe(32))
    bootstrap_password: SecretStr | None = None
    session_ttl_seconds: int = Field(default=2_592_000, ge=300)
    cors_origins: list[str] = Field(default_factory=list)
    auto_create_schema: bool = True
    static_dir: Path = Path(__file__).with_name("static")

    @field_validator("bootstrap_password", mode="before")
    @classmethod
    def empty_bootstrap_password_is_unset(cls, value: object) -> object:
        if isinstance(value, SecretStr) and not value.get_secret_value():
            return None
        return None if value == "" else value

    @model_validator(mode="after")
    def production_requires_auth(self) -> WebSettings:
        if self.environment != "production":
            return self
        self._validate_production_secrets()
        if self.auto_create_schema:
            raise ValueError(
                "FLASHCARDS_AUTO_CREATE_SCHEMA must be false in production"
            )
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "FLASHCARDS_DATABASE_URL must use postgresql+asyncpg:// "
                "in production"
            )
        return self

    def _validate_production_secrets(self) -> None:
        for name, secret in (
            ("session_secret", self.session_secret),
            ("auth_lookup_secret", self.auth_lookup_secret),
        ):
            env_name = f"FLASHCARDS_{name.upper()}"
            if name not in self.model_fields_set:
                raise ValueError(
                    f"{env_name} must be configured in production"
                )
            if len(secret.get_secret_value()) < 32:
                raise ValueError(
                    f"{env_name} must have at least 32 characters"
                )
        if self.session_secret == self.auth_lookup_secret:
            raise ValueError(
                "FLASHCARDS_SESSION_SECRET and FLASHCARDS_AUTH_LOOKUP_SECRET "
                "must differ in production"
            )


@lru_cache(maxsize=1)
def get_settings() -> WebSettings:
    return WebSettings()
