"""optimize jobs list

Revision ID: 20260524_0010
Revises: 20260524_0009
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260524_0010"
down_revision: str | None = "20260524_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_scores", sa.Column("recommendation", sa.String(length=40), nullable=True))
    op.execute(
        """
        UPDATE job_scores
        SET recommendation = CASE
            WHEN recommendation_tier = 'excluded' OR total_score < 50 THEN 'skip'
            WHEN total_score >= 70 THEN 'apply'
            ELSE 'maybe'
        END
        """
    )
    op.create_index("ix_jobs_first_seen_at", "jobs", ["first_seen_at"], unique=False)
    op.create_index("ix_jobs_remote_type", "jobs", ["remote_type"], unique=False)
    op.create_index("ix_job_analysis_role_family", "job_analysis", ["role_family"], unique=False)
    op.create_index("ix_job_scores_total_score", "job_scores", ["total_score"], unique=False)
    op.create_index("ix_job_scores_recommendation", "job_scores", ["recommendation"], unique=False)
    op.create_index("ix_job_scores_recommendation_tier", "job_scores", ["recommendation_tier"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_scores_recommendation_tier", table_name="job_scores")
    op.drop_index("ix_job_scores_recommendation", table_name="job_scores")
    op.drop_index("ix_job_scores_total_score", table_name="job_scores")
    op.drop_index("ix_job_analysis_role_family", table_name="job_analysis")
    op.drop_index("ix_jobs_remote_type", table_name="jobs")
    op.drop_index("ix_jobs_first_seen_at", table_name="jobs")
    op.drop_column("job_scores", "recommendation")
