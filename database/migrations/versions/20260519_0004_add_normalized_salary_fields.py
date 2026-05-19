"""add normalized salary fields

Revision ID: 20260519_0004
Revises: 20260519_0003
Create Date: 2026-05-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260519_0004"
down_revision: str | None = "20260519_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("salary_min_raw", sa.Numeric(12, 2), nullable=True))
    op.add_column("jobs", sa.Column("salary_max_raw", sa.Numeric(12, 2), nullable=True))
    op.add_column("jobs", sa.Column("salary_period", sa.String(length=20), nullable=True))
    op.add_column("jobs", sa.Column("normalized_annual_min", sa.Numeric(12, 2), nullable=True))
    op.add_column("jobs", sa.Column("normalized_annual_max", sa.Numeric(12, 2), nullable=True))
    op.execute("UPDATE jobs SET salary_min_raw = salary_min, salary_max_raw = salary_max, salary_period = 'year', normalized_annual_min = salary_min, normalized_annual_max = salary_max WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL")


def downgrade() -> None:
    op.drop_column("jobs", "normalized_annual_max")
    op.drop_column("jobs", "normalized_annual_min")
    op.drop_column("jobs", "salary_period")
    op.drop_column("jobs", "salary_max_raw")
    op.drop_column("jobs", "salary_min_raw")
