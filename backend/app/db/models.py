from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    skills: Mapped[list["UserSkill"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    target_roles: Mapped[list["TargetRole"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", cascade="all, delete-orphan")
    job_scores: Mapped[list["JobScore"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    missing_skills: Mapped[list["MissingSkill"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    saved_jobs: Mapped[list["SavedJob"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    ai_messages: Mapped[list["AIConversationMessage"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSkill(Base):
    __tablename__ = "user_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    proficiency: Mapped[str | None] = mapped_column(String(80))
    years_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    user: Mapped[User] = relationship(back_populates="skills")

    __table_args__ = (UniqueConstraint("user_id", "skill_name", name="uq_user_skills_user_skill"),)


class TargetRole(Base):
    __tablename__ = "target_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_title: Mapped[str] = mapped_column(String(180), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100", nullable=False)

    user: Mapped[User] = relationship(back_populates="target_roles")

    __table_args__ = (UniqueConstraint("user_id", "role_title", name="uq_target_roles_user_role"),)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    cv_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    experience: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    projects: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    education: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    preferred_roles: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    preferences: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    location_preference: Mapped[str | None] = mapped_column(String(255))
    remote_preference: Mapped[str | None] = mapped_column(String(80))
    salary_min_preference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max_preference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="profile")


class ExcludedTechnology(Base):
    __tablename__ = "excluded_technologies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class JobSource(Base):
    __tablename__ = "job_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    robots_url: Mapped[str | None] = mapped_column(String(500))
    terms_url: Mapped[str | None] = mapped_column(String(500))
    scraping_allowed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    permission_notes: Mapped[str | None] = mapped_column(Text)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    allowed_path_patterns: Mapped[list[str] | None] = mapped_column(JSON)
    job_link_patterns: Mapped[list[str] | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    scrape_runs: Mapped[list["ScrapeRun"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    raw_snapshots: Mapped[list["RawJobSnapshot"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    jobs_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list[str] | None] = mapped_column(JSON)
    parsed_jobs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    source: Mapped[JobSource] = relationship(back_populates="scrape_runs")


class RawJobSnapshot(Base):
    __tablename__ = "raw_job_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    source_job_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    raw_html: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    source: Mapped[JobSource] = relationship(back_populates="raw_snapshots")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(500))
    sector: Mapped[str | None] = mapped_column(String(120))
    size_band: Mapped[str | None] = mapped_column(String(80))

    jobs: Mapped[list["JobCompany"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    source_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(255))
    original_company: Mapped[str | None] = mapped_column(String(255))
    original_location: Mapped[str | None] = mapped_column(String(255))
    original_salary: Mapped[str | None] = mapped_column(String(255))
    original_external_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    remote_type: Mapped[str | None] = mapped_column(String(80), index=True)
    employment_type: Mapped[str | None] = mapped_column(String(80))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_min_raw: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max_raw: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_period: Mapped[str | None] = mapped_column(String(20))
    normalized_annual_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    normalized_annual_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    description_text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="active", server_default="active", nullable=False, index=True)
    application_status: Mapped[str] = mapped_column(
        String(40), default="not_started", server_default="not_started", nullable=False, index=True
    )
    availability_status: Mapped[str] = mapped_column(
        String(40), default="unknown", server_default="unknown", nullable=False, index=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    availability_reason: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(128), index=True)

    source: Mapped[JobSource] = relationship(back_populates="jobs")
    companies: Mapped[list["JobCompany"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    skills: Mapped[list["JobSkill"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    analysis: Mapped["JobAnalysis | None"] = relationship(back_populates="job", cascade="all, delete-orphan")
    scores: Mapped[list["JobScore"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    missing_skills: Mapped[list["MissingSkill"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    saved_jobs: Mapped[list["SavedJob"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    events: Mapped[list["JobEvent"]] = relationship(back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("source_id", "source_job_id", name="uq_jobs_source_source_job_id"),)


class JobCompany(Base):
    __tablename__ = "job_companies"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)

    job: Mapped[Job] = relationship(back_populates="companies")
    company: Mapped[Company] = relationship(back_populates="jobs")


class JobSkill(Base):
    __tablename__ = "job_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(120), nullable=False)
    skill_category: Mapped[str | None] = mapped_column(String(80))
    importance: Mapped[str | None] = mapped_column(String(40))
    evidence_text: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="skills")


class JobAnalysis(Base):
    __tablename__ = "job_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    seniority_level: Mapped[str | None] = mapped_column(String(80))
    role_family: Mapped[str | None] = mapped_column(String(120), index=True)
    role_focus: Mapped[str | None] = mapped_column(String(180))
    tools_detected: Mapped[list[str] | None] = mapped_column(JSON)
    responsibilities: Mapped[list[str] | None] = mapped_column(JSON)
    requirements: Mapped[list[str] | None] = mapped_column(JSON)
    nice_to_haves: Mapped[list[str] | None] = mapped_column(JSON)
    red_flags: Mapped[list[str] | None] = mapped_column(JSON)
    analysed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job: Mapped[Job] = relationship(back_populates="analysis")


class JobScore(Base):
    __tablename__ = "job_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, index=True)
    role_match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    skill_match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    experience_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    salary_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    location_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    freshness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    missing_skill_penalty: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    explanation: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(String(40), index=True)
    recommendation_tier: Mapped[str | None] = mapped_column(String(80), index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job: Mapped[Job] = relationship(back_populates="scores")
    user: Mapped[User] = relationship(back_populates="job_scores")

    __table_args__ = (UniqueConstraint("job_id", "user_id", name="uq_job_scores_job_user"),)


class JobRescoreRun(Base):
    __tablename__ = "job_rescore_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", server_default="queued", nullable=False, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    scored: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    total_jobs: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    completed_jobs: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class JobRescoreRunFailure(Base):
    __tablename__ = "job_rescore_run_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("job_rescore_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MissingSkill(Base):
    __tablename__ = "missing_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(120), nullable=False)
    importance: Mapped[str | None] = mapped_column(String(40))
    learning_priority: Mapped[str | None] = mapped_column(String(40))
    evidence_text: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="missing_skills")
    user: Mapped[User] = relationship(back_populates="missing_skills")


class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="saved", server_default="saved", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="saved_jobs")
    job: Mapped[Job] = relationship(back_populates="saved_jobs")

    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_saved_jobs_user_job"),)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    event_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job: Mapped[Job] = relationship(back_populates="events")


class AIConversationMessage(Base):
    __tablename__ = "ai_conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="ai_messages")
