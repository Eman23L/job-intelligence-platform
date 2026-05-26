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

    return Queue(
        settings.queue_name,
        connection=redis_connection(),
        default_timeout=settings.scrape_timeout_seconds,
        result_ttl=settings.rq_result_ttl_seconds,
        failure_ttl=settings.rq_failure_ttl_seconds,
    )


def enqueue_or_background(
    background_tasks: BackgroundTasks,
    func: Callable[..., Any],
    *args: Any,
    job_id: str | None = None,
    job_timeout: int | None = None,
    result_ttl: int | None = None,
    failure_ttl: int | None = None,
    **kwargs: Any,
) -> str:
    if queue_enabled():
        dotted = _dotted_path(func)
        timeout = job_timeout or timeout_for_function(func)
        job = rq_queue().enqueue_call(
            func=call_by_path,
            args=(dotted, *args),
            kwargs=kwargs,
            job_id=job_id,
            timeout=timeout,
            result_ttl=result_ttl or settings.rq_result_ttl_seconds,
            failure_ttl=failure_ttl or settings.rq_failure_ttl_seconds,
        )
        logger.info("queue_job_enqueued queue=%s rq_job_id=%s function=%s job_timeout=%s result_ttl=%s failure_ttl=%s", settings.queue_name, job.id, dotted, timeout, result_ttl or settings.rq_result_ttl_seconds, failure_ttl or settings.rq_failure_ttl_seconds)
        return str(job.id)
    background_tasks.add_task(func, *args, **kwargs)
    logger.info("background_task_scheduled function=%s queue_enabled=false", _dotted_path(func))
    return "background"


def call_by_path(path: str, *args: Any, **kwargs: Any) -> Any:
    module_name, function_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    func = getattr(module, function_name)
    return func(*args, **kwargs)


def timeout_for_function(func: Callable[..., Any]) -> int:
    dotted = _dotted_path(func)
    if "assist_apply" in dotted or "apply" in dotted:
        return settings.apply_timeout_seconds
    if "scrape" in dotted or "playwright" in dotted:
        return settings.scrape_timeout_seconds
    return settings.scrape_timeout_seconds


def _dotted_path(func: Callable[..., Any]) -> str:
    return f"{func.__module__}.{func.__name__}"
