"""Add apply strategy classification fields.

Revision ID: 20260525_0017
Revises: 20260525_0016
Create Date: 2026-05-25 15:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260525_0017"
down_revision: str | None = "20260525_0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("apply_strategy", sa.String(length=40), server_default="unknown", nullable=False))
    op.add_column("jobs", sa.Column("apply_difficulty", sa.String(length=40), server_default="unknown", nullable=False))
    op.add_column("jobs", sa.Column("apply_strategy_reason", sa.Text(), nullable=True))
    op.create_index("ix_jobs_apply_strategy", "jobs", ["apply_strategy"])
    op.create_index("ix_jobs_apply_difficulty", "jobs", ["apply_difficulty"])

    op.add_column("job_scores", sa.Column("apply_readiness_score", sa.Numeric(5, 2), nullable=True))
    op.create_index("ix_job_scores_apply_readiness_score", "job_scores", ["apply_readiness_score"])

    op.create_table(
        "job_apply_strategy_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="running", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("classified", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_apply_strategy_runs_status", "job_apply_strategy_runs", ["status"])
    op.create_index("ix_job_apply_strategy_runs_last_heartbeat_at", "job_apply_strategy_runs", ["last_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_job_apply_strategy_runs_last_heartbeat_at", table_name="job_apply_strategy_runs")
    op.drop_index("ix_job_apply_strategy_runs_status", table_name="job_apply_strategy_runs")
    op.drop_table("job_apply_strategy_runs")
    op.drop_index("ix_job_scores_apply_readiness_score", table_name="job_scores")
    op.drop_column("job_scores", "apply_readiness_score")
    op.drop_index("ix_jobs_apply_difficulty", table_name="jobs")
    op.drop_index("ix_jobs_apply_strategy", table_name="jobs")
    op.drop_column("jobs", "apply_strategy_reason")
    op.drop_column("jobs", "apply_difficulty")
    op.drop_column("jobs", "apply_strategy")
