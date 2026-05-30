# Autonomous Codex Loop

Codex may push directly to `main`, but only after running the relevant tests locally.

The deployment gate is GitHub Actions:

1. Codex makes the change locally.
2. Codex runs backend tests and any relevant frontend checks before pushing.
3. Codex pushes to `main`.
4. GitHub Actions runs backend tests again.
5. GitHub Actions runs frontend typecheck and build.
6. GitHub Actions triggers the Render web and worker deploy hooks only if all checks pass.

Render should not auto-deploy directly from a push to `main`. The GitHub Actions workflow is the deploy gate.

Required repository secrets:

- `RENDER_WEB_DEPLOY_HOOK_URL`
- `RENDER_WORKER_DEPLOY_HOOK_URL`
- `BACKEND_API_BASE_URL`
- `DIAGNOSTIC_ADMIN_TOKEN`

The workflow must not print these values. It logs only `deploying_web`, `deploying_worker`, and `deploy_hooks_triggered`.

## Diagnostics

The `Assist Apply Diagnostics` workflow can be run manually with:

- `application_id`
- `safe_mode`, default `true`

It calls the deployed FastAPI backend, not the frontend site:

```bash
POST ${BACKEND_API_BASE_URL}/diagnostics/assist-apply/<application_id>
GET ${BACKEND_API_BASE_URL}/diagnostics/assist-apply/runs/<run_id>
```

`APP_BASE_URL`, when used elsewhere, is the frontend URL. `BACKEND_API_BASE_URL` must be the FastAPI backend URL used by the frontend API client, for example the Render backend service URL configured through `NEXT_PUBLIC_API_BASE_URL`. Do not point diagnostics at the frontend domain unless that domain explicitly proxies `/diagnostics` to FastAPI.

The workflow first checks `${BACKEND_API_BASE_URL}/health`. If it receives a frontend/Next.js 404 page, the report is classified as `wrong_base_url_frontend_404`. Other health-check failures are classified as `backend_api_unreachable`.

The diagnostic probe runs inside Render and writes JSON and Markdown artifacts with environment summary, Redis/queue status, DB lookup status, JobServe URL resolution, browser status, JobServe navigation, modal/form detection, artifacts, and timings.

Safe mode never clicks the final JobServe Apply button unless `submit_allowed=true`.

If diagnostics fail, the workflow uploads artifacts and creates or updates a GitHub issue containing:

- `@codex fix this failure`
- `failed_phase`
- `exact_error`
- `traceback`
- `recommended_fix`
- artifact paths

Codex can then fix the reported failure, run tests locally, push to `main`, and let GitHub Actions deploy only after the gate passes.
