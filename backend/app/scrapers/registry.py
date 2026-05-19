from app.scrapers.base import BaseScraper
from app.scrapers.generic import GenericSourceAdapter
from app.scrapers.jobserve import JobServeSourceAdapter


class ScraperRegistry:
    def __init__(self) -> None:
        self._scrapers: dict[str, type[BaseScraper]] = {}

    def register(self, scraper: type[BaseScraper]) -> None:
        self._scrapers[scraper.source_name] = scraper

    def get(self, source_name: str) -> type[BaseScraper] | None:
        return self._scrapers.get(source_name)

    def names(self) -> list[str]:
        return sorted(self._scrapers)


registry = ScraperRegistry()


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, type[GenericSourceAdapter]] = {
            "generic": GenericSourceAdapter,
            "jobserve": JobServeSourceAdapter,
        }

    def register(self, source_type: str, adapter: type[GenericSourceAdapter]) -> None:
        self._adapters[source_type] = adapter

    def get(self, source_type: str) -> type[GenericSourceAdapter]:
        return self._adapters.get(source_type, GenericSourceAdapter)

    def names(self) -> list[str]:
        return sorted(self._adapters)


adapter_registry = AdapterRegistry()
