"""Let people choose the roles and location they search for, and track search progress.

Also drops the import key columns from 0024: LinkedIn and Indeed are now handled by Claude in Chrome.

Revision ID: 20261029_0025
Revises: 20261029_0024
Create Date: 2026-10-29 18:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20261029_0025"
down_revision: str | None = "20261029_0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("search_roles", sa.JSON(), nullable=True))
    op.add_column("user_profiles", sa.Column("search_location", sa.String(length=255), nullable=True))
    op.add_column("user_profiles", sa.Column("search_remote_only", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("user_profiles", sa.Column("search_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_profiles", sa.Column("search_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_index("ix_users_import_key_hash", table_name="users")
    op.drop_column("users", "import_key_last_used_at")
    op.drop_column("users", "import_key_created_at")
    op.drop_column("users", "import_key_hash")


def downgrade() -> None:
    op.add_column("users", sa.Column("import_key_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("import_key_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("import_key_last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_import_key_hash", "users", ["import_key_hash"], unique=True)
    op.drop_column("user_profiles", "search_finished_at")
    op.drop_column("user_profiles", "search_started_at")
    op.drop_column("user_profiles", "search_remote_only")
    op.drop_column("user_profiles", "search_location")
    op.drop_column("user_profiles", "search_roles")
