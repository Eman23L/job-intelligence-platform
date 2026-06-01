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
- `CODEX_GITHUB_TOKEN`

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

## Automatic Handoff

The canary and submit self-healing path now calls the protected backend endpoint:

```bash
POST ${BACKEND_API_BASE_URL}/diagnostics/handoff/codex
```

The endpoint is protected by `DIAGNOSTIC_ADMIN_TOKEN` and creates or updates a GitHub issue with the labels `codex`, `autonomous-canary`, and `assist-apply`. It reuses the existing open issue for the same `application_id` and `failed_phase`, stops after `MAX_AUTONOMOUS_FIX_ATTEMPTS_PER_ISSUE` attempts, and stops if the same `exact_error` repeats after two deploys.

The canary focuses on one JobServe application at a time. A failed submit, timeout, stall, browser launch failure, navigation failure, form/CV/final-submit failure, or missing success confirmation increments `autonomous_fix_attempt_count` for that application. The app retries the same application only after a Codex fix/deploy and fresh safe diagnostic while the count is below `MAX_AUTONOMOUS_FIX_ATTEMPTS_PER_APPLICATION`, default `3`. At attempt 3 it marks the application `blocked_after_3_attempts` and only then moves to the next eligible application.

The `Post Deploy Autonomous Verify` workflow runs after `CI Deploy Render` succeeds. It uses `CODEX_GITHUB_TOKEN` to find open `autonomous-canary` / `assist-apply` issues, reruns safe diagnostics for those application ids in Render, then calls the deployed backend autonomous verification route. It records or updates a Codex handoff issue on failure, and comments/closes matching handoff issues when verification passes.

Render web/worker services use `GITHUB_TOKEN`, `GITHUB_REPOSITORY=Eman23L/job-intelligence-platform`, `CODEX_MENTION=@codex`, and `DIAGNOSTIC_ADMIN_TOKEN`. GitHub Actions uses `CODEX_GITHUB_TOKEN` because repository secrets cannot be named with the reserved `GITHUB_` prefix.

Real JobServe submission still requires `AUTONOMOUS_REAL_SUBMIT_ENABLED=true`, and the submit limit remains `MAX_AUTONOMOUS_REAL_SUBMITS_PER_RUN`.
