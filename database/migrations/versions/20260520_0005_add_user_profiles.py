"""add user profiles

Revision ID: 20260520_0005
Revises: 20260519_0004
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0005"
down_revision: str | None = "20260519_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cv_text", sa.Text(), nullable=False),
        sa.Column("skills", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("experience", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("projects", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("education", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("preferred_roles", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("location_preference", sa.String(length=255), nullable=True),
        sa.Column("remote_preference", sa.String(length=80), nullable=True),
        sa.Column("salary_min_preference", sa.Numeric(12, 2), nullable=True),
        sa.Column("salary_max_preference", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
    )
    op.create_index("ix_user_profiles_user_id", "user_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_profiles_user_id", table_name="user_profiles")
    op.drop_table("user_profiles")
