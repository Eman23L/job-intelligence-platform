from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.db.models import JobScore
from app.services import browser_automation
from app.services.job_discovery import _sort_expressions
from tests.test_phase6_api import phase6_client


@pytest.mark.parametrize("sort", ["posted_at_desc", "salary_max_desc", "salary_min_desc", "total_score_desc"])
def test_descending_sorts_put_nulls_last_on_postgres(sort: str) -> None:
    score_subquery = select(JobScore.job_id, JobScore.total_score).subquery()
    compiled = [str(expr.compile(dialect=postgresql.dialect())) for expr in _sort_expressions(sort, score_subquery)]

    assert compiled[0].endswith("DESC NULLS LAST")


def test_location_and_company_filters_treat_wildcards_literally() -> None:
    with phase6_client() as (client, _):
        assert client.get("/jobs?location=%").json()["total_count"] == 0
        assert client.get("/jobs?company_name=_").json()["total_count"] == 0
        assert client.get("/jobs?location=").json()["total_count"] == 4


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_database_url_uses_psycopg_driver(scheme: str) -> None:
    settings = Settings(DATABASE_URL=f"{scheme}user:pass@db.example.com:5432/jobs")

    assert settings.database_url == "postgresql+psycopg://user:pass@db.example.com:5432/jobs"


def test_glob_prefers_highest_chromium_revision(tmp_path: Path, monkeypatch) -> None:
    for revision in ("999", "1194"):
        executable = tmp_path / f"chromium-{revision}" / "chrome-linux" / "chrome"
        executable.parent.mkdir(parents=True)
        executable.write_text("")
        executable.chmod(0o755)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    assert browser_automation._glob_chromium_executable_path() == tmp_path / "chromium-1194" / "chrome-linux" / "chrome"
