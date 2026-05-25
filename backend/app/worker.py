import logging

from rq import Worker

from app.config import settings
from app.services.queue import redis_connection


logging.basicConfig(level=settings.log_level)


def main() -> None:
    worker = Worker([settings.queue_name], connection=redis_connection())
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
