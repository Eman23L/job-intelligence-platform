"""Add apply dropdown preferences and threshold.

Revision ID: 20260526_0020
Revises: 20260525_0019
Create Date: 2026-05-26 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260526_0020"
down_revision: str | None = "20260525_0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("availability_notice", sa.String(length=80), nullable=True))
    op.add_column("user_profiles", sa.Column("salary_expectation_gbp", sa.Integer(), nullable=True))
    op.add_column("user_profiles", sa.Column("travel_distance_miles", sa.Integer(), nullable=True))
    op.add_column("user_profiles", sa.Column("minimum_apply_score", sa.Integer(), server_default="80", nullable=False))


def downgrade() -> None:
    op.drop_column("user_profiles", "minimum_apply_score")
    op.drop_column("user_profiles", "travel_distance_miles")
    op.drop_column("user_profiles", "salary_expectation_gbp")
    op.drop_column("user_profiles", "availability_notice")
