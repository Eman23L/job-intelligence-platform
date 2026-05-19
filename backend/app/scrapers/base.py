from abc import ABC, abstractmethod
from typing import Any


class BaseScraper(ABC):
    source_name: str
    base_url: str
    allowed_paths: list[str] = []
    rate_limit_per_minute: int = 10
    supports_pagination: bool = False

    @abstractmethod
    def discover_job_urls(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def fetch_job(self, url: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def parse_job(self, raw: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def normalise(self, parsed: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def save_raw(self, raw: Any, parsed: dict[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    def save_clean(self, normalised: dict[str, Any]) -> Any:
        raise NotImplementedError
