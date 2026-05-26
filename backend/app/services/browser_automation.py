from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.queue import redis_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserAutomationAvailability:
    available: bool
    error: str | None = None
    message: str | None = None
    chromium_executable_path: str | None = None


def browser_status() -> dict[str, Any]:
    redis_ok = redis_connected()
    chromium_path = chromium_executable_path()
    return {
        "queue_enabled": settings.queue_enabled,
        "redis_connected": redis_ok,
        "playwright_installed": playwright_installed(),
        "chromium_available": chromium_path is not None and Path(chromium_path).exists(),
        "worker_running": worker_running(redis_ok=redis_ok),
    }


def validate_browser_automation_availability(*, require_worker: bool = False) -> BrowserAutomationAvailability:
    if not settings.playwright_enabled:
        return BrowserAutomationAvailability(
            available=False,
            error="worker_unavailable",
            message="Browser automation worker is offline.",
        )
    if not playwright_installed():
        return BrowserAutomationAvailability(
            available=False,
            error="playwright_not_installed",
            message="Playwright is not installed in this environment.",
        )
    chromium_path = chromium_executable_path()
    if chromium_path is None or not Path(chromium_path).exists():
        return BrowserAutomationAvailability(
            available=False,
            error="chromium_not_installed",
            message="Playwright Chromium is not installed in this environment.",
            chromium_executable_path=chromium_path,
        )
    status = browser_status()
    if require_worker and settings.queue_enabled and not status["worker_running"]:
        return BrowserAutomationAvailability(
            available=False,
            error="worker_unavailable",
            message="Browser automation worker is offline.",
            chromium_executable_path=chromium_path,
        )
    return BrowserAutomationAvailability(available=True, chromium_executable_path=chromium_path)


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def chromium_executable_path() -> str | None:
    if not playwright_installed():
        return None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return playwright.chromium.executable_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("playwright_chromium_path_check_failed error=%s", exc)
        return None


def redis_connected() -> bool:
    if not settings.queue_enabled:
        return False
    try:
        redis_connection().ping()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_connectivity_check_failed error=%s", exc)
        return False


def worker_running(*, redis_ok: bool | None = None) -> bool:
    if not settings.queue_enabled:
        return False
    if redis_ok is False:
        return False
    try:
        connection = redis_connection()
        return bool(connection.keys("rq:worker:*"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("rq_worker_status_check_failed error=%s", exc)
        return False

