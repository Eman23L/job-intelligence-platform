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
- `PLAYWRIGHT_BROWSERS_PATH=/opt/render/.cache/ms-playwright`: shared Render cache path for installed browsers.

Build command for both Python services:
```bash
pip install -r requirements.txt && python -m playwright install chromium
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
- `GET /system/browser-status` reports queue, Redis, Playwright, Chromium, and worker health.
- Worker startup logs include Redis connectivity, queue status, Playwright availability, and the Chromium executable path.

Troubleshooting:
- `playwright_not_installed`: confirm `requirements.txt` includes `playwright` and the Render build ran `pip install -r requirements.txt`.
- `chromium_not_installed`: confirm the build command includes `python -m playwright install chromium` and `PLAYWRIGHT_BROWSERS_PATH` is set to `/opt/render/.cache/ms-playwright`.
- `worker_unavailable`: confirm the worker service is deployed, `QUEUE_ENABLED=true`, `REDIS_URL` points to the Render Redis service, and worker logs show it started against the expected queue.
- If Chromium launches fail under memory pressure, increase the worker instance size before retrying assisted applications.

The frontend continues polling the existing run status endpoints. No frontend contract changes are required when moving from fallback background tasks to RQ.
