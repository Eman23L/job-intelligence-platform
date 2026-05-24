"""add analytics indexes

Revision ID: 20260524_0008
Revises: 20260520_0007
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260524_0008"
down_revision: str | None = "20260520_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ai_conversation_messages_created_at",
        "ai_conversation_messages",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_scores_recommendation_tier_job_id",
        "job_scores",
        ["recommendation_tier", "job_id"],
        unique=False,
    )
    op.create_index(
        "ix_saved_jobs_status_job_id",
        "saved_jobs",
        ["status", "job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_saved_jobs_status_job_id", table_name="saved_jobs")
    op.drop_index("ix_job_scores_recommendation_tier_job_id", table_name="job_scores")
    op.drop_index("ix_ai_conversation_messages_created_at", table_name="ai_conversation_messages")
