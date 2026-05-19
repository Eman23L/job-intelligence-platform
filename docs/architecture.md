# UK Job Search Intelligence Platform

## Phase 1 Scope

Phase 1 creates the backend-first foundation for the platform:

- FastAPI application
- Health endpoint
- Environment-based configuration
- SQLAlchemy database setup
- Alembic migration setup
- pytest test setup
- Docker-ready local development structure

No frontend, scraper, worker, scoring, or job-source logic is implemented in this phase.

## Planned Architecture

The platform will use a backend-first design:

1. Source registry
2. Permitted source scraping workers
3. Raw job snapshot storage
4. Normalised job storage
5. Job description analysis
6. Skills comparison and scoring
7. Recommendation API
8. Dashboard frontend

## Phase 1 Runtime Components

- `backend/app/main.py`: FastAPI application factory
- `backend/app/config.py`: environment configuration
- `backend/app/db/session.py`: SQLAlchemy engine and session setup
- `backend/app/db/models.py`: SQLAlchemy declarative base
- `backend/app/api/health.py`: health endpoint
- `database/migrations`: Alembic migration environment
