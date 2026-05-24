"""add job fingerprint fields

Revision ID: 20260524_0012
Revises: 20260524_0011
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260524_0012"
down_revision: str | None = "20260524_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("original_title", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("original_company", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("original_location", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("original_salary", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("original_external_id", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE jobs
        SET original_title = title,
            original_company = company_name,
            original_location = location,
            original_external_id = source_job_id,
            original_salary = CASE
                WHEN salary_min_raw IS NOT NULL AND salary_max_raw IS NOT NULL AND salary_min_raw != salary_max_raw
                    THEN salary_currency || ' ' || salary_min_raw || '-' || salary_max_raw
                WHEN salary_min_raw IS NOT NULL
                    THEN salary_currency || ' ' || salary_min_raw
                WHEN salary_max_raw IS NOT NULL
                    THEN salary_currency || ' ' || salary_max_raw
                ELSE NULL
            END
        """
    )


def downgrade() -> None:
    op.drop_column("jobs", "original_external_id")
    op.drop_column("jobs", "original_salary")
    op.drop_column("jobs", "original_location")
    op.drop_column("jobs", "original_company")
    op.drop_column("jobs", "original_title")
