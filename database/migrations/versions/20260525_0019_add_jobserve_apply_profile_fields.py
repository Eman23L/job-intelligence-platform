"""Add JobServe assisted apply profile fields.

Revision ID: 20260525_0019
Revises: 20260525_0018
Create Date: 2026-05-25 17:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260525_0019"
down_revision: str | None = "20260525_0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column("user_profiles", sa.Column("first_name", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("last_name", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("phone", sa.String(length=80), nullable=True))
    op.add_column("user_profiles", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("country", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("work_status_uk", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("salary_expectation", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("travel_distance", sa.String(length=120), nullable=True))
    op.add_column("user_profiles", sa.Column("sponsorship_required", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("user_profiles", sa.Column("cv_file_path", sa.String(length=1000), nullable=True))
    op.add_column("user_profiles", sa.Column("cv_file_name", sa.String(length=255), nullable=True))
    op.add_column("user_profiles", sa.Column("cv_uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_applied_at", "jobs", ["applied_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_applied_at", table_name="jobs")
    op.drop_column("jobs", "applied_at")
    op.drop_column("user_profiles", "cv_uploaded_at")
    op.drop_column("user_profiles", "cv_file_name")
    op.drop_column("user_profiles", "cv_file_path")
    op.drop_column("user_profiles", "sponsorship_required")
    op.drop_column("user_profiles", "travel_distance")
    op.drop_column("user_profiles", "salary_expectation")
    op.drop_column("user_profiles", "work_status_uk")
    op.drop_column("user_profiles", "country")
    op.drop_column("user_profiles", "address")
    op.drop_column("user_profiles", "phone")
    op.drop_column("user_profiles", "last_name")
    op.drop_column("user_profiles", "first_name")
    op.drop_column("user_profiles", "email")
