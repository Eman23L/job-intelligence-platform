"""Let people choose the match score at which Claude applies to JobServe jobs without asking.

Revision ID: 20261104_0026
Revises: 20261029_0025
Create Date: 2026-11-04 14:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20261104_0026"
down_revision: str | None = "20261029_0025"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("auto_apply_min_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "auto_apply_min_score")
