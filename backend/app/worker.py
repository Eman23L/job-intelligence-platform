import logging

from rq import Worker

from app.config import settings
from app.services.browser_automation import browser_status, chromium_diagnostics
from app.services.queue import redis_connection


logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


def main() -> None:
    connection = redis_connection()
    redis_connected = bool(connection.ping())
    status = browser_status()
    chromium = chromium_diagnostics()
    logger.info(
        "worker_startup service_type=%s queue=%s queue_enabled=%s redis_connected=%s playwright_enabled=%s scrape_timeout_seconds=%s apply_timeout_seconds=%s playwright_step_timeout_ms=%s page_navigation_timeout_ms=%s result_ttl=%s failure_ttl=%s playwright_installed=%s chromium_available=%s playwright_browsers_path=%s chromium_executable_path=%s chromium_path_source=%s chromium_file_exists=%s chromium_file_executable=%s",
        settings.service_type,
        settings.queue_name,
        settings.queue_enabled,
        redis_connected,
        settings.playwright_enabled,
        settings.scrape_timeout_seconds,
        settings.apply_timeout_seconds,
        settings.playwright_step_timeout_ms,
        settings.page_navigation_timeout_ms,
        settings.rq_result_ttl_seconds,
        settings.rq_failure_ttl_seconds,
        status["playwright_installed"],
        status["chromium_available"],
        chromium["playwright_browsers_path"],
        chromium["chromium_executable_path"],
        chromium["chromium_path_source"],
        chromium["chromium_file_exists"],
        chromium["chromium_file_executable"],
    )
    if not status["chromium_available"]:
        logger.warning("worker_startup_chromium_unavailable ms_playwright_listing=%s", chromium["ms_playwright_listing"])
    worker = Worker([settings.queue_name], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
