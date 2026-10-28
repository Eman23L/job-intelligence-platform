"""Track applications per person instead of per job.

Revision ID: 20261028_0023
Revises: 20261028_0022
Create Date: 2026-10-28 18:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20261028_0023"
down_revision: str | None = "20261028_0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="jobserve", nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=40), server_default="to_apply", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_index("ix_applications_status", "applications", ["status"])

    # Until now application status lived on the shared jobs table and only the original
    # (lowest-id) user existed, so copy it across to that user's own application rows.
    op.execute(
        """
        INSERT INTO applications (user_id, job_id, source, title, company, location, url, status, applied_at)
        SELECT
            (SELECT MIN(id) FROM users),
            jobs.id,
            'jobserve',
            jobs.title,
            jobs.company_name,
            jobs.location,
            jobs.canonical_url,
            CASE jobs.application_status
                WHEN 'applied' THEN 'applied'
                WHEN 'skipped' THEN 'skipped'
                ELSE 'to_apply'
            END,
            CASE WHEN jobs.application_status = 'applied' THEN jobs.last_apply_attempt_at END
        FROM jobs
        WHERE jobs.application_status IN ('ready_to_apply', 'opened', 'applied', 'skipped', 'failed')
          AND EXISTS (SELECT 1 FROM users)
        """
    )

    # Saved jobs already marked applied (or further along) become application records too.
    op.execute(
        """
        INSERT INTO applications (user_id, job_id, source, title, company, location, url, status, applied_at)
        SELECT
            saved_jobs.user_id,
            jobs.id,
            'jobserve',
            jobs.title,
            jobs.company_name,
            jobs.location,
            jobs.canonical_url,
            CASE saved_jobs.status WHEN 'interviewing' THEN 'interviewing' WHEN 'offer' THEN 'offer' ELSE 'applied' END,
            saved_jobs.saved_at
        FROM saved_jobs
        JOIN jobs ON jobs.id = saved_jobs.job_id
        WHERE saved_jobs.status IN ('applied', 'interviewing', 'offer')
          AND NOT EXISTS (
              SELECT 1 FROM applications a WHERE a.user_id = saved_jobs.user_id AND a.job_id = saved_jobs.job_id
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_applications_status", table_name="applications")
    op.drop_index("ix_applications_job_id", table_name="applications")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
