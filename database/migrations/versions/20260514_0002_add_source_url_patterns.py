"""add source URL pattern fields

Revision ID: 20260514_0002
Revises: 20260513_0001
Create Date: 2026-05-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0002"
down_revision: str | None = "20260513_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_sources", sa.Column("allowed_path_patterns", sa.JSON(), nullable=True))
    op.add_column("job_sources", sa.Column("job_link_patterns", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_sources", "job_link_patterns")
    op.drop_column("job_sources", "allowed_path_patterns")
