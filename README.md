# UK Job Search Intelligence Platform

Backend-first job intelligence app for collecting permitted job listings, normalising salary and role data, analysing skills, scoring matches, and browsing results in a Next.js dashboard.

The project is intentionally split into:

- `backend/` - FastAPI app, SQLAlchemy models, scraper/adapters, analysis services, tests
- `frontend/` - Next.js dashboard
- `database/` - Alembic migrations
- `docker/` - backend container files
- `docs/` - architecture notes

## Features

- FastAPI API with PostgreSQL persistence
- Next.js dashboard for jobs, sources, salary analytics, saved jobs, and score views
- JobServe scraper using hidden search-page job IDs and AJAX detail endpoints
- Background scrape runs with polling status
- Salary normalisation for annual, day-rate, and hourly jobs
- Alembic migrations
- Pytest backend coverage

## Requirements

- Python 3.10+
- Node.js 18+
- PostgreSQL 14+
- npm

## Environment

Copy the example file and fill in local values:

```powershell
Copy-Item .env.example .env
```

Backend variables:

```env
APP_ENV=local
DEBUG=true
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/job_intelligence
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

Frontend variables:

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

Do not commit `.env` files. They are ignored by `.gitignore`.

## Local Backend Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Apply migrations:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Run the backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend
```

Health check:

```text
http://localhost:8000/health
```

## Local Frontend Setup

```powershell
cd frontend
npm ci
npm run dev
```

Open:

```text
http://localhost:3000/dashboard
```

## Tests And Checks

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
```

Frontend:

```powershell
cd frontend
npm run typecheck
npm run build
```

## Scraping

Scraping must only be run against sources you are permitted to access. Source records include permission notes and enabled state.

JobServe scraping uses:

1. Search page HTML to read the hidden `jobIDs` field
2. AJAX detail endpoint calls for each discovered ID
3. Background scrape runs so HTTP requests return immediately
4. `GET /scrape-runs/{id}` for progress polling

No Selenium is required.

## API Endpoints

Common endpoints:

- `GET /health`
- `GET /jobs`
- `GET /jobs/{id}`
- `GET /sources`
- `POST /sources/{id}/scrape-now`
- `GET /scrape-runs/{id}`
- `GET /analytics/salary`

## Vercel Frontend Deployment

Deploy only the `frontend/` folder to Vercel.

Recommended Vercel settings:

- Framework: Next.js
- Root directory: `frontend`
- Install command: `npm ci`
- Build command: `npm run build`

Set this environment variable in Vercel:

```env
NEXT_PUBLIC_API_BASE_URL=https://your-backend-domain.example
```

The frontend must not point to `localhost` in production.

## Backend Deployment

Deploy the FastAPI backend to a Python-capable host such as Render, Fly.io, Railway, a VPS, or a container service. Vercel is not recommended for this backend because scraping and background work are better suited to a persistent backend process.

Production backend environment variables:

```env
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://...
CORS_ORIGINS=https://your-vercel-app.vercel.app,https://your-custom-domain.example
```

`DATABASE_URL` is required in production. Startup performs a PostgreSQL connectivity check and exits with an explicit log message if the value is missing, still pointing at localhost, malformed, or unreachable.

Run migrations during deployment:

```bash
python -m alembic upgrade head
```

Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT --app-dir backend
```

## Docker

Local Docker Compose is available for backend plus PostgreSQL:

```powershell
docker compose up --build
```

Review `.env` before using Docker. Do not use production database credentials for local development.

## Security Notes

- `.env` and generated exports are ignored.
- Scraper outputs such as `jobserve_*.json` and `jobserve_*.csv` are ignored.
- Logs, caches, virtual environments, `node_modules`, `.next`, and Python bytecode are ignored.
- CORS is configured through `CORS_ORIGINS`.
- Never commit API keys, database URLs, cookies, exported scrape data, or production logs.
