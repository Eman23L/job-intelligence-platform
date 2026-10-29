"""Add per-person import keys for the daily LinkedIn & Indeed check, and keep row level security on.

Revision ID: 20261029_0024
Revises: 20261028_0023
Create Date: 2026-10-29 09:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20261029_0024"
down_revision: str | None = "20261028_0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("import_key_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("import_key_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("import_key_last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_import_key_hash", "users", ["import_key_hash"], unique=True)
    if op.get_bind().dialect.name == "postgresql":
        # Supabase serves the public schema over its REST API; with RLS on and no policies only the app's own
        # connection (the table owner) can use these tables. Run on every table so new ones are covered too.
        op.execute(
            """
            DO $$
            DECLARE t record;
            BEGIN
              FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
              END LOOP;
            END $$;
            """
        )


def downgrade() -> None:
    op.drop_index("ix_users_import_key_hash", table_name="users")
    op.drop_column("users", "import_key_last_used_at")
    op.drop_column("users", "import_key_created_at")
    op.drop_column("users", "import_key_hash")
