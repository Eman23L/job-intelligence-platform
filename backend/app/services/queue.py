from __future__ import annotations

from collections.abc import Callable
import importlib
import logging
from typing import Any

from fastapi import BackgroundTasks

from app.config import settings

logger = logging.getLogger(__name__)


def queue_enabled() -> bool:
    return settings.queue_enabled


def redis_connection():
    from redis import Redis

    return Redis.from_url(settings.redis_url)


def rq_queue():
    from rq import Queue

    return Queue(settings.queue_name, connection=redis_connection())


def enqueue_or_background(
    background_tasks: BackgroundTasks,
    func: Callable[..., Any],
    *args: Any,
    job_id: str | None = None,
    **kwargs: Any,
) -> str:
    if queue_enabled():
        dotted = _dotted_path(func)
        job = rq_queue().enqueue_call(func=call_by_path, args=(dotted, *args), kwargs=kwargs, job_id=job_id)
        logger.info("queue_job_enqueued queue=%s rq_job_id=%s function=%s", settings.queue_name, job.id, dotted)
        return str(job.id)
    background_tasks.add_task(func, *args, **kwargs)
    logger.info("background_task_scheduled function=%s queue_enabled=false", _dotted_path(func))
    return "background"


def call_by_path(path: str, *args: Any, **kwargs: Any) -> Any:
    module_name, function_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    func = getattr(module, function_name)
    return func(*args, **kwargs)


def _dotted_path(func: Callable[..., Any]) -> str:
    return f"{func.__module__}.{func.__name__}"
