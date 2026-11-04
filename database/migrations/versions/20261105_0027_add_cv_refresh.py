"""Keep the CV up to date: notes for Claude, when it last checked, and earlier versions to undo to.

Revision ID: 20261105_0027
Revises: 20261104_0026
Create Date: 2026-11-05 09:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20261105_0027"
down_revision: str | None = "20261104_0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("cv_notes", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("cv_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "cv_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cv_text", sa.Text(), nullable=False),
        sa.Column("cv_file_name", sa.String(length=255), nullable=True),
        sa.Column("cv_file_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("cv_file_mime_type", sa.String(length=120), nullable=True),
        sa.Column("changed_by", sa.String(length=20), nullable=False),
        sa.Column("change_note", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cv_versions_user_id", "cv_versions", ["user_id"])
    # Like every other table, nothing outside the app's own database connection can read it.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE cv_versions ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_cv_versions_user_id", table_name="cv_versions")
    op.drop_table("cv_versions")
    op.drop_column("user_profiles", "cv_checked_at")
    op.drop_column("user_profiles", "cv_notes")
