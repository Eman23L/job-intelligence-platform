import logging

from rq import Worker

from app.config import settings
from app.services.browser_automation import browser_status, chromium_executable_path
from app.services.queue import redis_connection


logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


def main() -> None:
    connection = redis_connection()
    redis_connected = bool(connection.ping())
    status = browser_status()
    logger.info(
        "worker_startup queue=%s queue_enabled=%s redis_connected=%s playwright_enabled=%s playwright_installed=%s chromium_available=%s chromium_executable_path=%s",
        settings.queue_name,
        settings.queue_enabled,
        redis_connected,
        settings.playwright_enabled,
        status["playwright_installed"],
        status["chromium_available"],
        chromium_executable_path(),
    )
    worker = Worker([settings.queue_name], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
