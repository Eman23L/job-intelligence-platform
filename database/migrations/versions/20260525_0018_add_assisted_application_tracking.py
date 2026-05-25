"""Add assisted application attempt tracking.

Revision ID: 20260525_0018
Revises: 20260525_0017
Create Date: 2026-05-25 16:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260525_0018"
down_revision: str | None = "20260525_0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("assisted_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("assisted_result", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("assisted_warnings", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("last_apply_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_last_apply_attempt_at", "jobs", ["last_apply_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_last_apply_attempt_at", table_name="jobs")
    op.drop_column("jobs", "last_apply_attempt_at")
    op.drop_column("jobs", "assisted_warnings")
    op.drop_column("jobs", "assisted_result")
    op.drop_column("jobs", "assisted_started_at")
