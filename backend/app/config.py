from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "UK Job Search Intelligence"
    app_version: str = "0.1.0"
    app_env: str = Field(default="local", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_origins: str = Field(
        default=(
            "http://localhost:3000,"
            "http://127.0.0.1:3000,"
            "http://localhost:3001,"
            "http://127.0.0.1:3001,"
            "https://job-intelligence-platform-drab.vercel.app"
        ),
        alias="CORS_ORIGINS",
    )

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/job_intelligence",
        alias="DATABASE_URL",
    )
    ai_provider: str = Field(default="groq", alias="AI_PROVIDER")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    ai_model: str = Field(default="llama-3.1-8b-instant", alias="AI_MODEL")
    ai_timeout_seconds: float = Field(default=30.0, alias="AI_TIMEOUT_SECONDS")
    ai_max_retries: int = Field(default=2, alias="AI_MAX_RETRIES")

    @field_validator("database_url", mode="before")
    @classmethod
    def use_installed_postgres_driver(cls, value: str) -> str:
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @staticmethod
    def _normalise_origin(value: str) -> str:
        return value.strip().strip("\"'").rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        raw_value: Any = self.cors_origins
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value.startswith("["):
                try:
                    raw_value = json.loads(value)
                except json.JSONDecodeError:
                    raw_value = value
            if isinstance(raw_value, str):
                return [
                    origin
                    for origin in (self._normalise_origin(item) for item in raw_value.split(","))
                    if origin
                ]
        if isinstance(raw_value, list):
            return [
                origin
                for origin in (self._normalise_origin(str(item)) for item in raw_value)
                if origin
            ]
        return []

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
