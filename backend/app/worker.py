import logging

from rq import Worker

from app.config import settings
from app.services.browser_automation import browser_status, chromium_diagnostics
from app.services.queue import redis_connection, redis_url_host


logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


def main() -> None:
    try:
        _run_worker()
    except Exception:  # noqa: BLE001
        logger.exception(
            "worker_startup_failed service_type=%s queue_enabled=%s queue=%s redis_host=%s playwright_enabled=%s",
            settings.service_type,
            settings.queue_enabled,
            settings.queue_name,
            redis_url_host(),
            settings.playwright_enabled,
        )
        raise


def _run_worker() -> None:
    logger.info(
        "worker_boot service_type=%s queue_enabled=%s queue=%s redis_host=%s playwright_enabled=%s",
        settings.service_type,
        settings.queue_enabled,
        settings.queue_name,
        redis_url_host(),
        settings.playwright_enabled,
    )
    if settings.service_type == "web":
        logger.warning("worker_service_type_misconfigured expected_service_type=worker actual_service_type=web")
    elif settings.service_type != "worker":
        logger.warning("worker_service_type_unexpected expected_service_type=worker actual_service_type=%s", settings.service_type)

    logger.info("worker_redis_connection_create_start redis_host=%s", redis_url_host())
    connection = redis_connection()
    logger.info("worker_redis_connection_create_success redis_host=%s", redis_url_host())
    logger.info("worker_redis_ping_start redis_host=%s", redis_url_host())
    redis_connected = bool(connection.ping())
    logger.info("worker_redis_ping_success redis_host=%s redis_connected=%s", redis_url_host(), redis_connected)

    logger.info("worker_browser_status_start playwright_enabled=%s", settings.playwright_enabled)
    status = browser_status()
    logger.info(
        "worker_browser_status_success playwright_installed=%s chromium_available=%s worker_running=%s",
        status["playwright_installed"],
        status["chromium_available"],
        status["worker_running"],
    )
    logger.info("worker_chromium_detection_start playwright_enabled=%s", settings.playwright_enabled)
    chromium = chromium_diagnostics()
    logger.info(
        "worker_chromium_detection_success playwright_browsers_path=%s chromium_executable_path=%s chromium_path_source=%s chromium_file_exists=%s chromium_file_executable=%s",
        chromium["playwright_browsers_path"],
        chromium["chromium_executable_path"],
        chromium["chromium_path_source"],
        chromium["chromium_file_exists"],
        chromium["chromium_file_executable"],
    )
    logger.info(
        "worker_startup service_type=%s queue=%s redis_host=%s queue_enabled=%s redis_connected=%s playwright_enabled=%s scrape_timeout_seconds=%s apply_timeout_seconds=%s playwright_step_timeout_ms=%s page_navigation_timeout_ms=%s result_ttl=%s failure_ttl=%s playwright_installed=%s chromium_available=%s playwright_browsers_path=%s chromium_executable_path=%s chromium_path_source=%s chromium_file_exists=%s chromium_file_executable=%s",
        settings.service_type,
        settings.queue_name,
        redis_url_host(),
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

    logger.info("worker_construction_start queue=%s redis_host=%s", settings.queue_name, redis_url_host())
    worker = Worker([settings.queue_name], connection=connection)
    logger.info("worker_construction_success queue=%s redis_host=%s", settings.queue_name, redis_url_host())
    logger.info("worker_work_start queue=%s redis_host=%s with_scheduler=true", settings.queue_name, redis_url_host())
    worker.work(with_scheduler=True)
    logger.warning("worker_work_exited queue=%s redis_host=%s", settings.queue_name, redis_url_host())


if __name__ == "__main__":
    main()
