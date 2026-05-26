"""Store uploaded CV file bytes for worker access.

Revision ID: 20260526_0021
Revises: 20260526_0020
Create Date: 2026-05-26 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260526_0021"
down_revision: str | None = "20260526_0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("cv_file_bytes", sa.LargeBinary(), nullable=True))
    op.add_column("user_profiles", sa.Column("cv_file_mime_type", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("cv_file_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "cv_file_size")
    op.drop_column("user_profiles", "cv_file_mime_type")
    op.drop_column("user_profiles", "cv_file_bytes")
