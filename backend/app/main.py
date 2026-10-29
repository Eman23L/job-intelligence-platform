import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.ai import router as ai_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.deps import get_current_user
from app.api.discovery import router as discovery_router
from app.api.applications import router as applications_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.profile import router as profile_router
from app.api.runs import router as runs_router
from app.api.saved_jobs import router as saved_jobs_router
from app.api.scrape_runs import router as scrape_runs_router
from app.api.scores import router as scores_router
from app.api.sources import router as sources_router
from app.api.suggestions import import_router as suggestions_import_router
from app.api.suggestions import router as suggestions_router
from app.config import settings
from app.logging_config import configure_logging


def _safe_database_url() -> str:
    value = settings.database_url
    if "@" not in value:
        return value
    scheme_and_auth, host = value.rsplit("@", 1)
    scheme = scheme_and_auth.split("://", 1)[0]
    return f"{scheme}://***:***@{host}"


def _validate_production_startup() -> None:
    if settings.app_env.lower() != "production":
        return
    logger = logging.getLogger(__name__)
    logger.info(
        "production startup diagnostics service_type=%s app_env=%s debug=%s cors_origins=%s database_url=%s",
        settings.service_type,
        settings.app_env,
        settings.debug,
        settings.cors_origin_list,
        _safe_database_url(),
    )
    if not settings.database_url.strip() or "localhost" in settings.database_url or "127.0.0.1" in settings.database_url:
        raise RuntimeError("DATABASE_URL must be set to the production PostgreSQL connection string on Render.")
    try:
        from app.db.session import engine

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("production startup database connectivity check failed")
        raise
    logger.info("production startup database connectivity check passed")


def _ensure_daily_search_scheduled() -> None:
    # The worker schedules each day's search after the last one; re-check on every web start in case the queue lost it.
    if not settings.queue_enabled:
        return
    try:
        from app.services.discovery import schedule_next_run
        from app.services.queue import rq_queue

        schedule_next_run(rq_queue())
    except Exception:  # noqa: BLE001 - the API should start even if Redis is unavailable.
        logging.getLogger(__name__).exception("daily_discovery_schedule_check_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _validate_production_startup()
    _ensure_daily_search_scheduled()
    yield


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Lets the browser read the CV's filename on download.
        expose_headers=["Content-Disposition"],
    )
    # Public: health checks and sign-in.
    app.include_router(health_router)
    app.include_router(auth_router)
    # Authenticated by a per-person import key instead of a session.
    app.include_router(suggestions_import_router)
    # Everything else needs a signed-in user.
    signed_in = [Depends(get_current_user)]
    for router in (
        sources_router,
        jobs_router,
        scores_router,
        scrape_runs_router,
        saved_jobs_router,
        analytics_router,
        applications_router,
        ai_router,
        profile_router,
        runs_router,
        discovery_router,
        suggestions_router,
    ):
        app.include_router(router, dependencies=signed_in)
    return app


app = create_app()
