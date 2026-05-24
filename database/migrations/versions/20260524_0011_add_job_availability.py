"""add job availability fields

Revision ID: 20260524_0011
Revises: 20260524_0010
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260524_0011"
down_revision: str | None = "20260524_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("availability_status", sa.String(length=40), server_default="unknown", nullable=False),
    )
    op.add_column("jobs", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("availability_reason", sa.Text(), nullable=True))
    op.create_index("ix_jobs_availability_status", "jobs", ["availability_status"], unique=False)
    op.create_index("ix_jobs_last_checked_at", "jobs", ["last_checked_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_last_checked_at", table_name="jobs")
    op.drop_index("ix_jobs_availability_status", table_name="jobs")
    op.drop_column("jobs", "availability_reason")
    op.drop_column("jobs", "last_checked_at")
    op.drop_column("jobs", "availability_status")
