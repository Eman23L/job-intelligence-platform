"""add application prepare runs

Revision ID: 20260525_0015
Revises: 20260525_0014
Create Date: 2026-05-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260525_0015"
down_revision: str | None = "20260525_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_prepare_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="running", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("queued", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_prepare_runs_status", "application_prepare_runs", ["status"], unique=False)
    op.create_index("ix_application_prepare_runs_last_heartbeat_at", "application_prepare_runs", ["last_heartbeat_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_application_prepare_runs_last_heartbeat_at", table_name="application_prepare_runs")
    op.drop_index("ix_application_prepare_runs_status", table_name="application_prepare_runs")
    op.drop_table("application_prepare_runs")
