import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.ai import router as ai_router
from app.api.analytics import router as analytics_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.profile import router as profile_router
from app.api.saved_jobs import router as saved_jobs_router
from app.api.scrape_runs import router as scrape_runs_router
from app.api.scores import router as scores_router
from app.api.sources import router as sources_router
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
        "production startup diagnostics app_env=%s debug=%s cors_origins=%s database_url=%s",
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _validate_production_startup()
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
    )
    app.include_router(health_router)
    app.include_router(sources_router)
    app.include_router(jobs_router)
    app.include_router(scores_router)
    app.include_router(scrape_runs_router)
    app.include_router(saved_jobs_router)
    app.include_router(analytics_router)
    app.include_router(ai_router)
    app.include_router(profile_router)
    return app


app = create_app()
