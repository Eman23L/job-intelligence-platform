"""add job availability runs

Revision ID: 20260525_0016
Revises: 20260525_0015
Create Date: 2026-05-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260525_0016"
down_revision: str | None = "20260525_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_availability_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="running", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_availability_runs_status", "job_availability_runs", ["status"], unique=False)
    op.create_index("ix_job_availability_runs_last_heartbeat_at", "job_availability_runs", ["last_heartbeat_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_availability_runs_last_heartbeat_at", table_name="job_availability_runs")
    op.drop_index("ix_job_availability_runs_status", table_name="job_availability_runs")
    op.drop_table("job_availability_runs")
