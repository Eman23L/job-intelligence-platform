from fastapi import APIRouter

from app.config import settings
from app.schemas.database import BrowserStatus
from app.services.browser_automation import browser_status

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


@router.get("/system/browser-status", response_model=BrowserStatus)
def get_browser_status() -> BrowserStatus:
    return BrowserStatus(**browser_status())
