"""add application status

Revision ID: 20260524_0009
Revises: 20260524_0008
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260524_0009"
down_revision: str | None = "20260524_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("application_status", sa.String(length=40), server_default="not_started", nullable=False),
    )
    op.create_index("ix_jobs_application_status", "jobs", ["application_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_application_status", table_name="jobs")
    op.drop_column("jobs", "application_status")
