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

Worker start command:
```bash
PYTHONPATH=backend python -m app.worker
```

The frontend continues polling the existing run status endpoints. No frontend contract changes are required when moving from fallback background tasks to RQ.
