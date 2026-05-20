"""add profile summary preferences

Revision ID: 20260520_0006
Revises: 20260520_0005
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0006"
down_revision: str | None = "20260520_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("summary", sa.Text(), server_default="", nullable=False))
    op.add_column("user_profiles", sa.Column("preferences", sa.JSON(), server_default="{}", nullable=False))


def downgrade() -> None:
    op.drop_column("user_profiles", "preferences")
    op.drop_column("user_profiles", "summary")
