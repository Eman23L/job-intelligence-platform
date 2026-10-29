from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Application, Base, Job, JobAnalysis, JobScore, ScrapeRun, User, UserProfile
from app.services import discovery
from app.services.discovery import Search


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _person(db, email: str, *, roles: list[str], minimum_score: int) -> User:
    user = User(email=email)
    db.add(user)
    db.flush()
    db.add(
        UserProfile(
            user_id=user.id,
            cv_text="Data engineer. Skills: Python, SQL, AWS, Airflow",
            skills=["Python", "SQL", "AWS", "Airflow"],
            preferred_roles=roles,
            location_preference="Milton Keynes / UK",
            minimum_apply_score=minimum_score,
        )
    )
    db.commit()
    return user


def _record(job_id: str, title: str, company: str = "Acme") -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": company,
        "location": "Milton Keynes",
        "url": f"https://www.example.invalid/jobs/{job_id}",
        "description": "Build data pipelines with Python and SQL on AWS. Airflow experience is a plus. Hybrid working.",
        "salary_min": None,
        "salary_max": None,
        "employment_type": "permanent",
        "posted_at": datetime.now(tz=timezone.utc),
    }


def test_next_run_is_the_next_weekday_morning_in_the_uk() -> None:
    friday_evening = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    monday_early = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)

    assert discovery.next_run_time(friday_evening).strftime("%a %H:%M") == "Mon 06:30"
    assert discovery.next_run_time(monday_early).date().isoformat() == "2026-11-02"


def test_searches_come_from_target_roles_and_location() -> None:
    profile = UserProfile(cv_text="x", preferred_roles=["Data Engineer", " ", "AI Engineer", "A", "B", "C"], location_preference="Milton Keynes / UK")

    assert discovery.searches_for(profile) == [
        Search("Data Engineer", "Milton Keynes"),
        Search("AI Engineer", "Milton Keynes"),
        Search("A", "Milton Keynes"),
        Search("B", "Milton Keynes"),
        Search("C", "Milton Keynes"),
    ]


def test_discover_saves_analyses_scores_and_records_progress() -> None:
    db = _session()
    keen = _person(db, "keen@example.invalid", roles=["Data Engineer"], minimum_score=0)
    other = _person(db, "other@example.invalid", roles=["Data Engineer"], minimum_score=0)
    searched: list[Search] = []

    def fake_feed(db, source, search):
        searched.append(search)
        records = [_record("reed-1", "Data Engineer"), _record("reed-2", "Analytics Engineer", "Beta")]
        return discovery._save_records(db, source, records)

    summary = discovery.discover(db, user_ids=[keen.id], feeds={"reed": fake_feed})

    assert searched == [Search("Data Engineer", "Milton Keynes")]
    assert summary["feeds"]["reed"]["created"] == 2
    jobs = db.scalars(select(Job)).all()
    assert all(db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id)) for job in jobs)
    # Everyone with a CV gets scores, but only the person who searched is marked as searching.
    assert db.scalar(select(JobScore).where(JobScore.user_id == other.id)) is not None
    keen_profile = db.scalar(select(UserProfile).where(UserProfile.user_id == keen.id))
    other_profile = db.scalar(select(UserProfile).where(UserProfile.user_id == other.id))
    assert keen_profile.search_started_at is not None and keen_profile.search_finished_at >= keen_profile.search_started_at
    assert other_profile.search_started_at is None
    assert db.scalar(select(Application)) is None
    run = db.scalar(select(ScrapeRun))
    assert run.status == "completed" and run.jobs_created == 2


def test_chosen_roles_and_location_override_the_cv() -> None:
    profile = UserProfile(
        cv_text="x", preferred_roles=["Warehouse Operative"], location_preference="Leeds", search_roles=["Junior Python Developer"], search_location="", search_remote_only=True
    )

    assert discovery.searches_for(profile) == [Search("Junior Python Developer", None, remote_only=True)]


def test_a_failing_feed_is_recorded_and_others_still_run() -> None:
    db = _session()
    _person(db, "keen@example.invalid", roles=["Data Engineer"], minimum_score=0)

    def broken(db, source, search):
        raise RuntimeError("feed down")

    def working(db, source, search):
        return discovery._save_records(db, source, [_record("adzuna-1", "Data Engineer")])

    summary = discovery.discover(db, feeds={"reed": broken, "adzuna": working})

    assert summary["feeds"]["reed"]["errors"] == 1
    assert summary["feeds"]["adzuna"]["created"] == 1
    statuses = {run.source.name: run.status for run in db.scalars(select(ScrapeRun)).all()}
    assert statuses == {"Reed": "failed", "Adzuna": "completed"}


class _Response:
    def __init__(self, body, ok=True):
        self._body = body
        self.ok = ok

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


def test_reed_results_are_parsed_with_full_descriptions(monkeypatch) -> None:
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("auth")))
        if url.endswith("/search"):
            return _Response(
                {
                    "results": [
                        {"jobId": 1, "jobTitle": "Data Engineer", "employerName": "Acme", "locationName": "Milton Keynes", "date": datetime.now().strftime("%d/%m/%Y"), "jobUrl": "https://www.reed.co.uk/jobs/data-engineer/1", "jobDescription": "short"},
                        {"jobId": 2, "jobTitle": "Old job", "date": "01/01/2020"},
                    ]
                }
            )
        return _Response({"jobDescription": "<p>Build <b>pipelines</b> in Python.</p>", "yearlyMinimumSalary": 50000, "yearlyMaximumSalary": 65000})

    monkeypatch.setattr(discovery.settings, "reed_api_key", "test-key")
    monkeypatch.setattr(discovery.requests, "get", fake_get)

    records = discovery.fetch_reed(Search("Data Engineer", "Milton Keynes"))

    assert [record["title"] for record in records] == ["Data Engineer"]  # the 2020 job is too old
    assert "Build pipelines in Python." in records[0]["description"]
    assert str(records[0]["salary_min"]) == "50000" and records[0]["id"] == "reed-1"
    assert calls[0][1] == ("test-key", "")


def test_adzuna_ignores_predicted_salaries(monkeypatch) -> None:
    body = {
        "results": [
            {"id": "9", "title": "<strong>Data</strong> Engineer", "company": {"display_name": "Acme"}, "location": {"display_name": "Milton Keynes"}, "description": "Python and SQL pipelines for analytics teams.", "salary_min": 40000, "salary_max": 50000, "salary_is_predicted": "1", "created": "2026-10-28T10:00:00Z"}
        ]
    }
    monkeypatch.setattr(discovery.requests, "get", lambda url, **kwargs: _Response(body))

    [record] = discovery.fetch_adzuna(Search("Data Engineer", None))

    assert record["title"] == "Data Engineer"
    assert record["salary_min"] is None
    assert record["url"] == "https://www.adzuna.co.uk/jobs/details/9"


def test_schedule_next_run_queues_once_per_day() -> None:
    class FakeQueue:
        def __init__(self):
            self.jobs = {}

        def fetch_job(self, job_id):
            return self.jobs.get(job_id)

        def enqueue_at(self, run_at, func, *args, job_id, job_timeout):
            self.jobs[job_id] = (run_at, args)

    queue = FakeQueue()
    first = discovery.schedule_next_run(queue)
    discovery.schedule_next_run(queue)

    assert len(queue.jobs) == 1
    [(run_at, args)] = queue.jobs.values()
    assert run_at == first and args == ("app.services.discovery.run_scheduled_discovery",)


def test_feeds_need_their_keys(monkeypatch) -> None:
    monkeypatch.setattr(discovery.settings, "reed_api_key", "")
    monkeypatch.setattr(discovery.settings, "adzuna_app_id", "id")
    monkeypatch.setattr(discovery.settings, "adzuna_app_key", "key")

    assert discovery.feeds_configured() == {"jobserve": True, "reed": False, "adzuna": True}
    assert set(discovery.default_feeds()) == {"jobserve", "adzuna"}


def test_only_titles_that_fit_a_target_role_are_suggested() -> None:
    from app.services.applications import title_fits_roles

    roles = ["Python Developer", "Software Engineer", "Data Engineer", "Automation Engineer", "AI Engineer"]
    fits = ["Senior Data Engineer", "Python Automation Engineer", "BI Developer", "Machine Learning Engineer", "Backend Developer", "Power Platform Developer"]
    misfits = ["Technical Installer - Security Engineer CCTV", "Linux Engineer - SC Cleared", "Infrastructure Engineer (Citrix and VMware)", "Senior Project Manager"]

    assert all(title_fits_roles(title, roles) for title in fits)
    assert not any(title_fits_roles(title, roles) for title in misfits)
    assert title_fits_roles("Anything at all", [])
    # Every key word of a role has to be there, so a broad word like "support" doesn't let unrelated jobs in.
    assert title_fits_roles("IT Support Analyst", ["IT Support"])
    assert not title_fits_roles("Project Support Officer", ["IT Support"])
    assert not title_fits_roles("Head of IT Project Delivery", ["IT Support"])

