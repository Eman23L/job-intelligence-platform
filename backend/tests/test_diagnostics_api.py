from fastapi.testclient import TestClient

from app.api import diagnostics as diagnostics_api
from app.config import settings
from app.diagnostics import assist_apply_runs
from app.main import app


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "diagnostic_admin_token", "secret-token")
    monkeypatch.setattr(assist_apply_runs, "RUN_ROOT", tmp_path)
    return TestClient(app)


def test_diagnostic_endpoint_rejects_missing_or_invalid_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    missing = client.post("/diagnostics/assist-apply/645", json={"user_id": 1, "safe_mode": True, "submit_allowed": False})
    invalid = client.post(
        "/diagnostics/assist-apply/645",
        headers={"Authorization": "Bearer wrong"},
        json={"user_id": 1, "safe_mode": True, "submit_allowed": False},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_diagnostic_endpoint_creates_run(tmp_path, monkeypatch) -> None:
    enqueued = []

    def fake_enqueue(background_tasks, func, *args, **kwargs):
        enqueued.append((func, args, kwargs))
        return "rq-diagnostic"

    monkeypatch.setattr(diagnostics_api, "enqueue_or_background", fake_enqueue)
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/diagnostics/assist-apply/645",
        headers={"Authorization": "Bearer secret-token"},
        json={"user_id": 1, "safe_mode": True, "submit_allowed": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    assert body["status"] == "queued"
    assert body["latest_progress"]["rq_job_id"] == "rq-diagnostic"
    assert enqueued
    assert enqueued[0][1][1] == 645


def test_diagnostic_polling_returns_queued_running_and_final_states(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    run_id = assist_apply_runs.new_run_id()
    headers = {"Authorization": "Bearer secret-token"}
    assist_apply_runs.write_run_status(run_id, status="queued", application_id=645, user_id=1, safe_mode=True, submit_allowed=False, latest_progress={"phase": "queued"})

    queued = client.get(f"/diagnostics/assist-apply/runs/{run_id}", headers=headers)
    assist_apply_runs.write_run_status(run_id, status="running", application_id=645, user_id=1, safe_mode=True, submit_allowed=False, latest_progress={"phase": "db_lookup"})
    running = client.get(f"/diagnostics/assist-apply/runs/{run_id}", headers=headers)
    assist_apply_runs.write_run_status(
        run_id,
        status="failed",
        application_id=645,
        user_id=1,
        safe_mode=True,
        submit_allowed=False,
        latest_progress={"phase": "redis_queue"},
        final_report={"overall_status": "failed", "failed_phase": "redis_queue", "exact_error": "temporary DNS failure", "recommended_fix": "Run remotely in Render."},
        markdown_summary="# Assist Apply Probe\n",
        artifact_links=["backend/runtime/assist_apply_diagnostics/runs/example/latest_assist_apply_probe.json"],
    )
    final = client.get(f"/diagnostics/assist-apply/runs/{run_id}", headers=headers)

    assert queued.json()["status"] == "queued"
    assert running.json()["status"] == "running"
    assert final.json()["status"] == "failed"
    assert final.json()["final_report"]["failed_phase"] == "redis_queue"
    assert final.json()["final_report"]["exact_error"]
    assert final.json()["final_report"]["recommended_fix"]


def test_remote_failed_report_includes_required_fields(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    run_id = assist_apply_runs.new_run_id()
    assist_apply_runs.write_run_status(
        run_id,
        status="failed",
        application_id=645,
        user_id=1,
        safe_mode=True,
        submit_allowed=False,
        final_report={"overall_status": "failed", "failed_phase": "jobserve_navigation", "exact_error": "chromium missing", "recommended_fix": "Install Chromium in Render."},
    )

    response = client.get(f"/diagnostics/assist-apply/runs/{run_id}", headers={"Authorization": "Bearer secret-token"})

    report = response.json()["final_report"]
    assert report["failed_phase"]
    assert report["exact_error"]
    assert report["recommended_fix"]


def test_github_workflow_remote_diagnostic_payload_is_valid() -> None:
    workflow = open(".github/workflows/assist-apply-diagnostics.yml", encoding="utf-8").read()

    assert "BACKEND_API_BASE_URL" in workflow
    assert "APP_BASE_URL" not in workflow
    assert "DIAGNOSTIC_ADMIN_TOKEN" in workflow
    assert 'POST "$backend_base/diagnostics/assist-apply/$APPLICATION_ID"' in workflow
    assert '"$backend_base/diagnostics/assist-apply/runs/$run_id"' in workflow
    assert '\\"user_id\\":1' in workflow
    assert '\\"submit_allowed\\":false' in workflow
    assert '"$backend_base/health"' in workflow


def test_workflow_classifies_frontend_404_wrong_base_url() -> None:
    workflow = open(".github/workflows/assist-apply-diagnostics.yml", encoding="utf-8").read()

    assert "wrong_base_url_frontend_404" in workflow
    assert "Next.js 404" in workflow
    assert "frontend app" in workflow


def test_backend_health_check_failure_produces_useful_report() -> None:
    workflow = open(".github/workflows/assist-apply-diagnostics.yml", encoding="utf-8").read()

    assert "backend_api_unreachable" in workflow
    assert "Backend health check failed with HTTP" in workflow
    assert "backend_health_response.txt" in workflow


def test_private_redis_failure_in_github_does_not_block_remote_diagnostics() -> None:
    workflow = open(".github/workflows/assist-apply-diagnostics.yml", encoding="utf-8").read()

    assert "python -m app.diagnostics.assist_apply_probe" not in workflow
    assert "DATABASE_URL" not in workflow
    assert "REDIS_URL" not in workflow
    assert "remote_diagnostic_endpoint_unavailable" in workflow


def test_post_deploy_autonomous_verify_workflow_uses_remote_backend_and_handoff() -> None:
    workflow = open(".github/workflows/post-deploy-autonomous-verify.yml", encoding="utf-8").read()

    assert "workflow_run" in workflow
    assert "CI Deploy Render" in workflow
    assert "BACKEND_API_BASE_URL" in workflow
    assert "CODEX_GITHUB_TOKEN" in workflow
    assert "Rerun affected safe diagnostics" in workflow
    assert "/diagnostics/assist-apply/{app_id}" in workflow
    assert "$backend_base/applications/autonomous-real-submit" in workflow
    assert "$backend_base/diagnostics/handoff/codex" in workflow
    assert "DIAGNOSTIC_ADMIN_TOKEN" in workflow
    assert "post_deploy_autonomous_verify" in workflow


def test_diagnostic_endpoint_route_exists() -> None:
    routes = {getattr(route, "path", "") for route in app.routes}

    assert "/diagnostics/assist-apply/{application_id}" in routes
    assert "/diagnostics/assist-apply/runs/{run_id}" in routes
    assert "/diagnostics/handoff/codex" in routes
    assert "/applications/{application_id}/assist-apply/diagnostics" in routes
    assert "/applications/{application_id}/assist-apply/diagnostics/{run_id}" in routes


def test_codex_handoff_endpoint_rejects_missing_or_invalid_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    missing = client.post("/diagnostics/handoff/codex", json={"report": {"failed_phase": "x"}})
    invalid = client.post("/diagnostics/handoff/codex", headers={"Authorization": "Bearer wrong"}, json={"report": {"failed_phase": "x"}})

    assert missing.status_code == 401
    assert invalid.status_code == 403


def test_codex_handoff_endpoint_creates_or_updates_issue(tmp_path, monkeypatch) -> None:
    import app.api.diagnostics as diagnostics_api

    monkeypatch.setattr(diagnostics_api, "create_or_update_codex_handoff", lambda report: {"status": "created", "issue_url": "https://github.com/owner/repo/issues/10"})
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/diagnostics/handoff/codex",
        headers={"Authorization": "Bearer secret-token"},
        json={"report": {"application_id": 645, "failed_phase": "jobserve_navigation", "exact_error": "modal missing", "recommended_fix": "Fix selector."}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["issue_url"].endswith("/10")


def test_application_submit_failure_diagnostic_forces_safe_mode(tmp_path, monkeypatch, db_session) -> None:
    from fastapi import BackgroundTasks

    import app.api.applications as applications_api
    from app.db.models import Job, User

    user = User(email="user@example.com")
    job = Job(source_id=1, source_job_id="job-1", canonical_url="https://www.jobserve.com/job", title="AI Engineer", application_status="ready_to_apply")
    db_session.add_all([user, job])
    db_session.commit()
    db_session.refresh(job)

    captured = []

    def fake_enqueue(background_tasks, func, *args, **kwargs):
        captured.append((func, args, kwargs))
        return "rq-safe-diagnostic"

    monkeypatch.setattr(assist_apply_runs, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(applications_api, "enqueue_or_background", fake_enqueue)
    response = applications_api.start_failed_submit_diagnostic(job.id, BackgroundTasks(), db_session)

    assert response.status == "queued"
    assert captured
    args = captured[0][1]
    assert args[1] == job.id
    assert args[3] is True
    assert args[4] is False


def test_application_diagnostic_poll_returns_status(tmp_path, monkeypatch, db_session) -> None:
    import app.api.applications as applications_api
    from app.db.models import Job

    job = Job(source_id=1, source_job_id="job-1", canonical_url="https://www.jobserve.com/job", title="AI Engineer", application_status="ready_to_apply")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    monkeypatch.setattr(assist_apply_runs, "RUN_ROOT", tmp_path)
    run_id = assist_apply_runs.new_run_id()
    assist_apply_runs.write_run_status(
        run_id,
        status="failed",
        application_id=job.id,
        user_id=1,
        safe_mode=True,
        submit_allowed=False,
        final_report={"failed_phase": "jobserve_navigation", "exact_error": "modal missing", "recommended_fix": "Inspect artifacts."},
    )

    response = applications_api.get_failed_submit_diagnostic(job.id, run_id, db_session)

    assert response.status == "failed"
    assert response.final_report["failed_phase"] == "jobserve_navigation"
