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
- One simple flow: add your CV and the jobs you want, see the jobs found for you, apply to one or all, and see what's been applied for (see "Applying for jobs")
- Daily search every weekday morning (JobServe, plus Reed and Adzuna when their API keys are set)
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
- `GET /applications`, `POST /applications`, `PATCH /applications/{id}`, `GET /applications/{id}/pack`

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
PYTHON_VERSION=3.11.9
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://...
CORS_ORIGINS=https://your-vercel-app.vercel.app,https://your-custom-domain.example
```

`DATABASE_URL` is required in production. Startup performs a PostgreSQL connectivity check and exits with an explicit log message if the value is missing, still pointing at localhost, malformed, or unreachable.

Render should log the selected Python version during build. Look for a line like:

```text
Python 3.11.9
```

If Render still logs Python 3.14, confirm the service root directory is the repository root and that the service is using the committed `render.yaml` blueprint or has `PYTHON_VERSION=3.11.9` set in the Render dashboard.

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

## Accounts

The app is invite-only. Every page and API route except `/health` and `/auth/*` requires a signed-in user, and each person's CV, scores, saved jobs and AI chat are private to them. Job listings and sources are shared; only the owner can add, delete or hide them.

1. Set `OWNER_SETUP_CODE` on the backend to a long random value.
2. Open the site, choose **Set up the owner account**, and enter that code with your name, email and password. This claims the existing data (CV, saved jobs, scores) and makes you the owner.
3. To add someone, go to **Account → Invite someone** and send them the link. Each link works once and expires after `INVITE_DAYS` (default 14).

Sign-in sessions last `SESSION_DAYS` (default 30). Passwords are hashed with scrypt; session tokens and invite codes are stored only as SHA-256 hashes.

## Applying for jobs

The app doesn't submit applications itself. Claude in Chrome fills in each form in your own browser and you press Submit.

1. **My CV**: add your CV (file and text), then say which roles you want (up to five, even ones your CV doesn't show yet) and where. Saving runs a search straight away.
2. **Find jobs**: the jobs found for those roles in the last two weeks, best match to your CV first, with a **New search** button. The search also runs on the worker at 6:30am UK time every weekday. A job is listed when its title contains every key word of one of your roles (close synonyms count: data also covers analytics and BI, AI also covers machine learning). Press **Apply** on a job or **Apply to all** to queue them for Claude, or **Not for me** to hide one.
3. **Applications**: jobs queued for Claude, and the ones you've applied for, with how each is going. "Show instructions for Claude" gives Claude in Chrome one set of instructions: work through the queue (each job's **Apply now** page has your details, your CV and the description), then apply on LinkedIn and Indeed for your roles and log each one. Save them as a Claude in Chrome shortcut and schedule it daily to have it run every morning. When Claude marks a job as applied it shows as **✓ Applied** on Find jobs too.

Reed and Adzuna need free API keys: set `REED_API_KEY`, `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` on both Render services. Each person's lists are private to them. Older tools (overview, all jobs, AI advisor, skill gaps, salary, sources, activity) are under **More tools** in the menu.

## Security Notes

- `.env` and generated exports are ignored.
- Scraper outputs such as `jobserve_*.json` and `jobserve_*.csv` are ignored.
- Logs, caches, virtual environments, `node_modules`, `.next`, and Python bytecode are ignored.
- CORS is configured through `CORS_ORIGINS`.
- Never commit API keys, database URLs, cookies, exported scrape data, or production logs.
