from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analytics import router as analytics_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.saved_jobs import router as saved_jobs_router
from app.api.scrape_runs import router as scrape_runs_router
from app.api.scores import router as scores_router
from app.api.sources import router as sources_router
from app.config import settings
from app.logging_config import configure_logging


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
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
    return app


app = create_app()
