"""Validated runtime configuration for Atlas Agent."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and an optional `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    model: str = "openai:gpt-4.1-mini"
    model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    custom_model_configured: bool = False
    data_dir: Path = Path(".atlas/data")
    workspace_dir: Path = Path(".atlas/workspace")
    memory_enabled: bool = True
    memory_collection: str = Field(default="atlas_memories", pattern=r"^[a-zA-Z0-9_-]+$")
    memory_recall_limit: int = Field(default=5, ge=1, le=20)

    require_code_approval: bool = True
    require_overwrite_approval: bool = True
    code_execution_backend: Literal["docker", "disabled"] = "disabled"
    code_timeout_seconds: int = Field(default=10, ge=1, le=30)
    code_memory_mb: int = Field(default=256, ge=64, le=1024)
    max_agent_iterations: int = Field(default=8, ge=1, le=24)
    max_review_cycles: int = Field(default=2, ge=0, le=5)
    max_tool_output_chars: int = Field(default=12_000, ge=1_000, le=50_000)
    max_file_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    search_max_results: int = Field(default=5, ge=1, le=10)
    thread_lock_timeout_seconds: int = Field(default=120, ge=1, le=3_600)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65_535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    tavily_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TAVILY_API_KEY", "ATLAS_TAVILY_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "ATLAS_OPENAI_API_KEY"),
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "ATLAS_ANTHROPIC_API_KEY"),
    )

    @field_validator("model")
    @classmethod
    def model_must_include_provider(cls, value: str) -> str:
        """Require provider-qualified model names so deployment is reproducible."""
        cleaned = value.strip()
        if len(cleaned) > 200 or ":" not in cleaned:
            raise ValueError("model must use the 'provider:model-name' form")
        provider, model_name = cleaned.split(":", 1)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", provider):
            raise ValueError("model provider must be a valid identifier")
        if not model_name or any(character.isspace() for character in model_name):
            raise ValueError("model name must be non-empty and contain no whitespace")
        return cleaned

    @field_validator("tavily_api_key", "openai_api_key", "anthropic_api_key", mode="before")
    @classmethod
    def blank_secrets_are_unconfigured(cls, value: object) -> object | None:
        """Treat copied-but-unedited sample credentials as absent."""
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return value if raw.strip() else None

    @field_validator("data_dir", "workspace_dir", mode="before")
    @classmethod
    def normalize_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def vector_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def sandbox_path(self) -> Path:
        return self.data_dir / "sandboxes"

    @property
    def thread_lock_dir(self) -> Path:
        return self.data_dir / "thread-locks"

    @property
    def file_lock_dir(self) -> Path:
        return self.data_dir / "file-locks"

    @property
    def model_provider(self) -> str:
        return self.model.split(":", 1)[0].casefold()

    @property
    def model_name(self) -> str:
        return self.model.split(":", 1)[1]

    @property
    def model_credential_is_configured(self) -> bool:
        """Report credential presence without reading or exposing credential values."""
        if self.model_provider == "openai":
            return bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())
        if self.model_provider == "anthropic":
            return bool(
                self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip()
            )
        return self.custom_model_configured

    @property
    def model_integration_is_available(self) -> bool:
        """Require the selected provider integration before reporting model readiness."""
        module_name = {
            "openai": "langchain_openai",
            "anthropic": "langchain_anthropic",
        }.get(self.model_provider)
        if module_name is None:
            return self.custom_model_configured
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            return False

    @property
    def model_is_configured(self) -> bool:
        """Report complete local model setup without contacting the provider."""
        return self.model_credential_is_configured and self.model_integration_is_available

    @property
    def model_api_key(self) -> SecretStr | None:
        """Return the provider credential as an opaque secret for client construction."""
        if self.model_provider == "openai":
            return self.openai_api_key
        if self.model_provider == "anthropic":
            return self.anthropic_api_key
        return None

    def ensure_directories(self) -> None:
        """Create only the application-owned directories required at runtime."""
        for path in (
            self.data_dir,
            self.workspace_dir,
            self.vector_path,
            self.sandbox_path,
            self.thread_lock_dir,
            self.file_lock_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def checkpoint_thread_id(self, user_id: str, thread_id: str) -> str:
        """Build a non-enumerable checkpoint key that is namespaced per user."""
        digest = hashlib.sha256(f"{user_id}\x00{thread_id}".encode()).hexdigest()[:32]
        return f"atlas-{digest}"

    def thread_lock_path(self, user_id: str, thread_id: str) -> Path:
        """Return the process-shared lock file for one tenant-scoped thread."""
        return self.thread_lock_dir / f"{self.checkpoint_thread_id(user_id, thread_id)}.lock"


def get_settings() -> Settings:
    """Create a fresh settings object, allowing tests and CLIs to override the environment."""
    return Settings()
