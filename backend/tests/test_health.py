from fastapi.testclient import TestClient

from app.api import health
from app.main import app
from app.services import browser_automation


def test_health_check_returns_success() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_browser_status_endpoint_returns_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "browser_status",
        lambda: {
            "queue_enabled": True,
            "redis_connected": True,
            "playwright_installed": True,
            "chromium_available": True,
            "worker_running": True,
        },
    )
    client = TestClient(app)

    response = client.get("/system/browser-status")

    assert response.status_code == 200
    assert response.json() == {
        "queue_enabled": True,
        "redis_connected": True,
        "playwright_installed": True,
        "chromium_available": True,
        "worker_running": True,
    }


def test_playwright_installed_check(monkeypatch) -> None:
    monkeypatch.setattr(browser_automation.importlib.util, "find_spec", lambda name: object() if name == "playwright" else None)

    assert browser_automation.playwright_installed() is True


def test_chromium_executable_check(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "chromium"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(browser_automation.settings, "playwright_enabled", True)
    monkeypatch.setattr(browser_automation, "playwright_installed", lambda: True)
    monkeypatch.setattr(browser_automation, "chromium_executable_path", lambda: str(executable))

    availability = browser_automation.validate_browser_automation_availability()

    assert availability.available is True
    assert availability.chromium_executable_path == str(executable)


def test_queue_enabled_check(monkeypatch) -> None:
    class FakeRedis:
        def ping(self) -> bool:
            return True

    monkeypatch.setattr(browser_automation.settings, "queue_enabled", True)
    monkeypatch.setattr(browser_automation, "redis_connection", lambda: FakeRedis())

    assert browser_automation.redis_connected() is True
