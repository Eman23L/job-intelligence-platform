"""create core schema

Revision ID: 20260513_0001
Revises:
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260513_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "excluded_technologies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_excluded_technologies_name"),
    )

    op.create_table(
        "job_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("robots_url", sa.String(length=500), nullable=True),
        sa.Column("terms_url", sa.String(length=500), nullable=True),
        sa.Column("scraping_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("permission_notes", sa.Text(), nullable=True),
        sa.Column("rate_limit_per_minute", sa.Integer(), server_default="10", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_job_sources_name"),
    )

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=True),
        sa.Column("size_band", sa.String(length=80), nullable=True),
        sa.UniqueConstraint("name", name="uq_companies_name"),
    )

    op.create_table(
        "user_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("proficiency", sa.String(length=80), nullable=True),
        sa.Column("years_experience", sa.Numeric(4, 1), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "skill_name", name="uq_user_skills_user_skill"),
    )
    op.create_index("ix_user_skills_user_id", "user_skills", ["user_id"])

    op.create_table(
        "target_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_title", sa.String(length=180), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "role_title", name="uq_target_roles_user_role"),
    )
    op.create_index("ix_target_roles_user_id", "target_roles", ["user_id"])

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobs_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["job_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_scrape_runs_source_id", "scrape_runs", ["source_id"])

    op.create_table(
        "raw_job_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["job_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_raw_job_snapshots_source_id", "raw_job_snapshots", ["source_id"])
    op.create_index("ix_raw_job_snapshots_content_hash", "raw_job_snapshots", ["content_hash"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("remote_type", sa.String(length=80), nullable=True),
        sa.Column("employment_type", sa.String(length=80), nullable=True),
        sa.Column("salary_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("salary_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="active", nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["job_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_id", "source_job_id", name="uq_jobs_source_source_job_id"),
    )
    op.create_index("ix_jobs_source_id", "jobs", ["source_id"])
    op.create_index("ix_jobs_source_id_source_job_id", "jobs", ["source_id", "source_job_id"])
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])
    op.create_index("ix_jobs_posted_at", "jobs", ["posted_at"])
    op.create_index("ix_jobs_last_seen_at", "jobs", ["last_seen_at"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "job_companies",
        sa.Column("job_id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "job_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(length=120), nullable=False),
        sa.Column("skill_category", sa.String(length=80), nullable=True),
        sa.Column("importance", sa.String(length=40), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_skills_job_id", "job_skills", ["job_id"])
    op.create_index("ix_job_skills_job_id_skill_name", "job_skills", ["job_id", "skill_name"])

    op.create_table(
        "job_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("seniority_level", sa.String(length=80), nullable=True),
        sa.Column("role_family", sa.String(length=120), nullable=True),
        sa.Column("role_focus", sa.String(length=180), nullable=True),
        sa.Column("tools_detected", sa.JSON(), nullable=True),
        sa.Column("responsibilities", sa.JSON(), nullable=True),
        sa.Column("requirements", sa.JSON(), nullable=True),
        sa.Column("nice_to_haves", sa.JSON(), nullable=True),
        sa.Column("red_flags", sa.JSON(), nullable=True),
        sa.Column("analysed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", name="uq_job_analysis_job_id"),
    )

    op.create_table(
        "job_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("role_match_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("skill_match_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("experience_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("salary_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("location_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("freshness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("missing_skill_penalty", sa.Numeric(5, 2), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("recommendation_tier", sa.String(length=80), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "user_id", name="uq_job_scores_job_user"),
    )
    op.create_index("ix_job_scores_job_id", "job_scores", ["job_id"])
    op.create_index("ix_job_scores_user_id", "job_scores", ["user_id"])
    op.create_index("ix_job_scores_user_id_total_score", "job_scores", ["user_id", "total_score"])
    op.create_index("ix_job_scores_user_id_recommendation_tier", "job_scores", ["user_id", "recommendation_tier"])

    op.create_table(
        "missing_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(length=120), nullable=False),
        sa.Column("importance", sa.String(length=40), nullable=True),
        sa.Column("learning_priority", sa.String(length=40), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_missing_skills_job_id", "missing_skills", ["job_id"])
    op.create_index("ix_missing_skills_user_id", "missing_skills", ["user_id"])
    op.create_index("ix_missing_skills_user_id_skill_name", "missing_skills", ["user_id", "skill_name"])

    op.create_table(
        "saved_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="saved", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_saved_jobs_user_job"),
    )
    op.create_index("ix_saved_jobs_job_id", "saved_jobs", ["job_id"])
    op.create_index("ix_saved_jobs_user_id", "saved_jobs", ["user_id"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_saved_jobs_user_id", table_name="saved_jobs")
    op.drop_index("ix_saved_jobs_job_id", table_name="saved_jobs")
    op.drop_table("saved_jobs")
    op.drop_index("ix_missing_skills_user_id_skill_name", table_name="missing_skills")
    op.drop_index("ix_missing_skills_user_id", table_name="missing_skills")
    op.drop_index("ix_missing_skills_job_id", table_name="missing_skills")
    op.drop_table("missing_skills")
    op.drop_index("ix_job_scores_user_id_recommendation_tier", table_name="job_scores")
    op.drop_index("ix_job_scores_user_id_total_score", table_name="job_scores")
    op.drop_index("ix_job_scores_user_id", table_name="job_scores")
    op.drop_index("ix_job_scores_job_id", table_name="job_scores")
    op.drop_table("job_scores")
    op.drop_table("job_analysis")
    op.drop_index("ix_job_skills_job_id_skill_name", table_name="job_skills")
    op.drop_index("ix_job_skills_job_id", table_name="job_skills")
    op.drop_table("job_skills")
    op.drop_table("job_companies")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_last_seen_at", table_name="jobs")
    op.drop_index("ix_jobs_posted_at", table_name="jobs")
    op.drop_index("ix_jobs_content_hash", table_name="jobs")
    op.drop_index("ix_jobs_source_id_source_job_id", table_name="jobs")
    op.drop_index("ix_jobs_source_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_raw_job_snapshots_content_hash", table_name="raw_job_snapshots")
    op.drop_index("ix_raw_job_snapshots_source_id", table_name="raw_job_snapshots")
    op.drop_table("raw_job_snapshots")
    op.drop_index("ix_scrape_runs_source_id", table_name="scrape_runs")
    op.drop_table("scrape_runs")
    op.drop_index("ix_target_roles_user_id", table_name="target_roles")
    op.drop_table("target_roles")
    op.drop_index("ix_user_skills_user_id", table_name="user_skills")
    op.drop_table("user_skills")
    op.drop_table("companies")
    op.drop_table("job_sources")
    op.drop_table("excluded_technologies")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
