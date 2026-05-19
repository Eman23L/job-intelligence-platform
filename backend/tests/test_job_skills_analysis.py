from sqlalchemy import select

from app.db.models import ExcludedTechnology, Job, JobAnalysis, JobSkill, JobSource
from app.services.analysis import analyse_job
from app.services.skills import detect_excluded_technologies, extract_skills


DATA_ENGINEER_DESCRIPTION = """
Responsibilities include building ETL data pipelines and improving data quality.
Requirements: must have Python, SQL, PostgreSQL, Airflow, Docker and AWS.
Nice to have Databricks and PySpark.
"""

ANALYTICS_ENGINEER_DESCRIPTION = """
Responsibilities include creating data models for finance teams.
Required SQL and dbt experience. Nice to have Microsoft Fabric and lakehouse knowledge.
"""

INTERNAL_TOOLS_DESCRIPTION = """
You will build internal tools with FastAPI, React, TypeScript and REST APIs.
Requirements include PostgreSQL and GitHub Actions.
"""

AI_AUTOMATION_DESCRIPTION = """
Responsibilities include designing AI automation and LLM workflows.
Required RAG, agents, evaluation, guardrails and prompt management.
"""

WORKFLOW_AUTOMATION_DESCRIPTION = """
You will deliver workflow automation and systems integration.
Must have Python, JSON, Requests and Playwright for controlled internal automation.
"""

EXCLUDED_TECH_DESCRIPTION = """
Requirements: must have Power Platform and Power Automate experience.
Desirable Power BI knowledge. The team occasionally mentions Dataverse in legacy documents.
"""


def test_skill_extraction_uses_controlled_taxonomy() -> None:
    skills = extract_skills(INTERNAL_TOOLS_DESCRIPTION)
    names = {skill.name for skill in skills}

    assert {"FastAPI", "React", "TypeScript", "REST APIs", "PostgreSQL", "GitHub Actions"}.issubset(names)


def test_excluded_technology_detection_classifies_severity() -> None:
    mentions = detect_excluded_technologies(
        EXCLUDED_TECH_DESCRIPTION,
        ["Power Platform", "Power Automate", "Power BI", "Dataverse"],
    )
    severities = {mention.name: mention.severity for mention in mentions}

    assert severities["Power Platform"] == "essential requirement"
    assert severities["Power Automate"] == "essential requirement"
    assert severities["Power BI"] == "nice-to-have"
    assert severities["Dataverse"] == "minor mention"


def test_job_analysis_creation_and_job_skills_creation(db_session) -> None:
    job = _create_job(db_session, "AI Automation Engineer", AI_AUTOMATION_DESCRIPTION)
    _seed_excluded_technologies(db_session)

    analysis = analyse_job(db_session, job)
    skills = db_session.scalars(select(JobSkill).where(JobSkill.job_id == job.id)).all()

    assert analysis.role_family == "AI Automation Engineer"
    assert analysis.role_focus == "AI automation"
    assert analysis.seniority_level is None
    assert {"LLM workflows", "RAG", "agents", "evaluation", "guardrails", "prompt management"}.issubset(
        {skill.skill_name for skill in skills}
    )


def test_analysis_flags_excluded_technologies_without_deleting_job(db_session) -> None:
    job = _create_job(db_session, "Automation Consultant", EXCLUDED_TECH_DESCRIPTION)
    _seed_excluded_technologies(db_session)

    analysis = analyse_job(db_session, job)

    assert db_session.get(Job, job.id) is not None
    assert analysis.red_flags
    assert any("Power Platform: essential requirement" in flag for flag in analysis.red_flags)
    assert any("Power BI: nice-to-have" in flag for flag in analysis.red_flags)


def test_fixture_descriptions_classify_requested_role_families() -> None:
    cases = [
        ("Data Engineer", DATA_ENGINEER_DESCRIPTION, "Data Engineer"),
        ("Analytics Engineer", ANALYTICS_ENGINEER_DESCRIPTION, "Analytics Engineer"),
        ("Internal Tools Engineer", INTERNAL_TOOLS_DESCRIPTION, "Internal Tools Engineer"),
        ("AI Automation Engineer", AI_AUTOMATION_DESCRIPTION, "AI Automation Engineer"),
        ("Workflow Automation Engineer", WORKFLOW_AUTOMATION_DESCRIPTION, "Workflow Automation Engineer"),
    ]

    for title, description, expected in cases:
        job = Job(
            id=1,
            source_id=1,
            source_job_id="fixture",
            canonical_url="https://example.invalid/jobs/fixture",
            title=title,
            description_text=description,
        )
        assert analyse_job.__module__ == "app.services.analysis"
        from app.services.normalisation import classify_role_family

        assert classify_role_family(job.title, job.description_text or "") == expected


def _create_job(db_session, title: str, description: str) -> Job:
    source = JobSource(name=f"{title} Source", base_url="https://example.invalid", source_type="fixture")
    db_session.add(source)
    db_session.flush()
    job = Job(
        source_id=source.id,
        source_job_id=title.lower().replace(" ", "-"),
        canonical_url=f"https://example.invalid/jobs/{title.lower().replace(' ', '-')}",
        title=title,
        company_name="Example Ltd",
        location="Remote UK",
        description_text=description,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _seed_excluded_technologies(db_session) -> None:
    for name in [
        "Power Platform",
        "Power Apps",
        "Power Automate",
        "Power BI",
        "Dataverse",
        "Power Fx",
        "DAX",
        "Power Query",
        "Copilot Studio",
        "AI Builder",
    ]:
        db_session.add(ExcludedTechnology(name=name))
    db_session.commit()
