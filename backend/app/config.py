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
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    queue_enabled: bool = Field(default=False, alias="QUEUE_ENABLED")
    queue_name: str = Field(default="default", alias="QUEUE_NAME")
    rq_result_ttl_seconds: int = Field(default=3600, alias="RQ_RESULT_TTL_SECONDS")
    rq_failure_ttl_seconds: int = Field(default=86400, alias="RQ_FAILURE_TTL_SECONDS")
    scrape_timeout_seconds: int = Field(default=600, alias="SCRAPE_TIMEOUT_SECONDS")
    apply_timeout_seconds: int = Field(default=900, alias="APPLY_TIMEOUT_SECONDS")
    playwright_step_timeout_ms: int = Field(default=30000, alias="PLAYWRIGHT_STEP_TIMEOUT_MS")
    page_navigation_timeout_ms: int = Field(default=60000, alias="PAGE_NAVIGATION_TIMEOUT_MS")
    playwright_enabled: bool = Field(default=False, alias="PLAYWRIGHT_ENABLED")
    service_type: str = Field(default="web", alias="SERVICE_TYPE")
    diagnostic_admin_token: str = Field(default="", alias="DIAGNOSTIC_ADMIN_TOKEN")
    autonomous_real_submit_enabled: bool = Field(default=False, alias="AUTONOMOUS_REAL_SUBMIT_ENABLED")
    max_autonomous_real_submits_per_run: int = Field(default=1, alias="MAX_AUTONOMOUS_REAL_SUBMITS_PER_RUN")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_repository: str = Field(default="", alias="GITHUB_REPOSITORY")
    codex_mention: str = Field(default="@codex", alias="CODEX_MENTION")
    max_autonomous_fix_attempts_per_issue: int = Field(default=5, alias="MAX_AUTONOMOUS_FIX_ATTEMPTS_PER_ISSUE")

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
