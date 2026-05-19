from app.db.models import Base


def test_core_models_are_registered() -> None:
    expected_tables = {
        "users",
        "user_skills",
        "target_roles",
        "excluded_technologies",
        "job_sources",
        "scrape_runs",
        "raw_job_snapshots",
        "companies",
        "jobs",
        "job_companies",
        "job_skills",
        "job_analysis",
        "job_scores",
        "missing_skills",
        "saved_jobs",
        "job_events",
    }

    assert expected_tables.issubset(set(Base.metadata.tables))
