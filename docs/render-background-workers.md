# Render Background Workers

Production long-running workflows use Redis + RQ.

Services:
- `job-intelligence-backend`: FastAPI web service. Creates run rows, enqueues work, returns `run_id`.
- `job-intelligence-worker`: RQ worker service. Processes queued jobs and updates run progress.
- `job-intelligence-redis`: Redis instance used by RQ.

Required environment variables on both web and worker:
- `REDIS_URL`: Redis connection string.
- `QUEUE_ENABLED=true`: use RQ. Set `false` only for local fallback to FastAPI `BackgroundTasks`.
- `QUEUE_NAME=default`: queue name consumed by the worker.
- `PLAYWRIGHT_ENABLED=true`: allow assisted browser automation.
- `PLAYWRIGHT_BROWSERS_PATH=0`: install browsers hermetically into the deployed Python Playwright package directory.
- `SERVICE_TYPE=web` on the FastAPI service and `SERVICE_TYPE=worker` on the RQ worker.

Build command for both Python services:
```bash
pip install -r requirements.txt && PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium
```

Worker start command:
```bash
PYTHONPATH=backend python -m app.worker
```

Redis is required in production. The web service enqueues work, and the worker service must be running for queued background workflows and browser automation diagnostics to report healthy.

Recommended Render sizing:
- Web service: at least 512 MB RAM.
- Worker service: at least 1 GB RAM for Playwright Chromium, preferably 2 GB if multiple browser sessions or scoring jobs may overlap.
- Redis: the managed Render Redis service is sufficient for the current RQ queue.

Browser automation diagnostics:
- `GET /system/browser-status` on the backend reports queue, Redis, local service Playwright/Chromium, and worker health. The current backend URL is `https://job-intelligence-ai-63rj.onrender.com/system/browser-status`.
- From the Vercel frontend domain, `/system/browser-status` is proxied to the backend.
- Worker startup logs include service type, Redis connectivity, queue status, `PLAYWRIGHT_BROWSERS_PATH`, Playwright availability, the resolved Chromium executable path, and file existence/executable checks.

Troubleshooting:
- `playwright_not_installed`: confirm `requirements.txt` includes `playwright` and the Render build ran `pip install -r requirements.txt`.
- `chromium_not_installed`: confirm the build command includes `PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium` and runtime `PLAYWRIGHT_BROWSERS_PATH` is also `0`.
- If Chromium exists but is not executable, check the worker startup log fields `chromium_file_exists`, `chromium_file_executable`, and `ms_playwright_listing`.
- If Chromium launches but fails due to missing Linux packages, switch the build command to `PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install --with-deps chromium` or use Docker with a Playwright-compatible base image.
- `worker_unavailable`: confirm the worker service is deployed, `QUEUE_ENABLED=true`, `REDIS_URL` points to the Render Redis service, and worker logs show it started against the expected queue.
- If Chromium launches fail under memory pressure, increase the worker instance size before retrying assisted applications.

When `QUEUE_ENABLED=true`, assisted apply requests are queued to RQ and run in the worker. The web service should not launch Chromium for assisted applications in production.
