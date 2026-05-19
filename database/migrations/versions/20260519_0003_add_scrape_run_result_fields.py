"""add scrape run result fields

Revision ID: 20260519_0003
Revises: 20260514_0002
Create Date: 2026-05-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260519_0003"
down_revision: str | None = "20260514_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scrape_runs", sa.Column("errors", sa.JSON(), nullable=True))
    op.add_column("scrape_runs", sa.Column("parsed_jobs", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("scrape_runs", "parsed_jobs")
    op.drop_column("scrape_runs", "errors")
