"""harden job rescore runs

Revision ID: 20260525_0014
Revises: 20260524_0013
Create Date: 2026-05-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260525_0014"
down_revision: str | None = "20260524_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_rescore_runs", sa.Column("total_jobs", sa.Integer(), server_default="0", nullable=False))
    op.add_column("job_rescore_runs", sa.Column("completed_jobs", sa.Integer(), server_default="0", nullable=False))
    op.add_column("job_rescore_runs", sa.Column("failed_jobs", sa.Integer(), server_default="0", nullable=False))
    op.add_column("job_rescore_runs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE job_rescore_runs SET total_jobs = total, completed_jobs = scored, failed_jobs = failed")
    op.execute("UPDATE job_rescore_runs SET status = 'queued' WHERE status = 'running' AND total = 0 AND scored = 0 AND failed = 0")
    op.create_index("ix_job_rescore_runs_last_heartbeat_at", "job_rescore_runs", ["last_heartbeat_at"], unique=False)
    op.create_table(
        "job_rescore_run_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["job_rescore_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_rescore_run_failures_job_id", "job_rescore_run_failures", ["job_id"], unique=False)
    op.create_index("ix_job_rescore_run_failures_run_id", "job_rescore_run_failures", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_rescore_run_failures_run_id", table_name="job_rescore_run_failures")
    op.drop_index("ix_job_rescore_run_failures_job_id", table_name="job_rescore_run_failures")
    op.drop_table("job_rescore_run_failures")
    op.drop_index("ix_job_rescore_runs_last_heartbeat_at", table_name="job_rescore_runs")
    op.drop_column("job_rescore_runs", "last_heartbeat_at")
    op.drop_column("job_rescore_runs", "failed_jobs")
    op.drop_column("job_rescore_runs", "completed_jobs")
    op.drop_column("job_rescore_runs", "total_jobs")
