from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserBase(BaseModel):
    email: str


class UserCreate(UserBase):
    pass


class UserRead(UserBase, ORMModel):
    id: int
    created_at: datetime


class UserSkillBase(BaseModel):
    user_id: int
    skill_name: str
    category: str | None = None
    proficiency: str | None = None
    years_experience: Decimal | None = None
    is_primary: bool = False


class UserSkillCreate(UserSkillBase):
    pass


class UserSkillRead(UserSkillBase, ORMModel):
    id: int


class TargetRoleBase(BaseModel):
    user_id: int
    role_title: str
    priority: int = 100


class TargetRoleCreate(TargetRoleBase):
    pass


class TargetRoleRead(TargetRoleBase, ORMModel):
    id: int


class CVProfileCreate(BaseModel):
    cv_text: str = Field(min_length=1)


class UserProfileRead(ORMModel):
    id: int
    user_id: int
    cv_text: str
    summary: str
    skills: list[str]
    experience: list[str]
    projects: list[str]
    education: list[str]
    preferred_roles: list[str]
    preferences: dict[str, str]
    location_preference: str | None = None
    remote_preference: str | None = None
    salary_min_preference: Decimal | None = None
    salary_max_preference: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class ExcludedTechnologyBase(BaseModel):
    name: str
    reason: str | None = None


class ExcludedTechnologyCreate(ExcludedTechnologyBase):
    pass


class ExcludedTechnologyRead(ExcludedTechnologyBase, ORMModel):
    id: int
    created_at: datetime


class JobSourceBase(BaseModel):
    name: str
    base_url: str
    source_type: str
    robots_url: str | None = None
    terms_url: str | None = None
    scraping_allowed: bool = False
    permission_notes: str | None = None
    rate_limit_per_minute: int = 10
    allowed_path_patterns: list[str] | None = None
    job_link_patterns: list[str] | None = None
    enabled: bool = False
    last_reviewed_at: datetime | None = None


class JobSourceCreate(JobSourceBase):
    pass


class JobSourceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    source_type: str | None = None
    robots_url: str | None = None
    terms_url: str | None = None
    scraping_allowed: bool | None = None
    permission_notes: str | None = None
    rate_limit_per_minute: int | None = None
    enabled: bool | None = None
    last_reviewed_at: datetime | None = None


class JobSourceRead(JobSourceBase, ORMModel):
    id: int
    created_at: datetime


class SourcePermissionValidation(BaseModel):
    source_id: int
    can_scrape: bool
    reasons: list[str]
    warnings: list[str]


class SourceFromUrlCreate(BaseModel):
    name: str
    base_url: str
    source_type: str
    permission_notes: str
    scraping_allowed: bool
    rate_limit_per_minute: int = 10
    allowed_path_patterns: list[str] | None = None
    job_link_patterns: list[str] | None = None


class SourceTestResult(BaseModel):
    can_fetch: bool
    status_code: int | None = None
    page_title: str | None = None
    links_found_count: int = 0
    likely_job_links_count: int = 0
    sample_job_links: list[str]
    discovered_job_ids: list[str] = Field(default_factory=list)
    warnings: list[str]
    errors: list[str]


class ScrapeNowRequest(BaseModel):
    start_url: str | None = None
    max_pages: int = 10
    max_jobs: int = 20
    delay_seconds: float = 8
    dry_run: bool = False


class ParsedJobSummary(BaseModel):
    source_job_id: str | None = None
    title: str | None = None
    company_name: str | None = None
    location: str | None = None
    canonical_url: str | None = None


class ScrapeNowResult(BaseModel):
    scrape_run_id: int | None = None
    jobs_found: int
    jobs_created: int
    jobs_updated: int
    jobs_skipped: int
    discovered_job_ids: list[str] = Field(default_factory=list)
    parsed_jobs: list[ParsedJobSummary] = Field(default_factory=list)
    errors: list[str]
    warnings: list[str]


class ScrapeStartResult(BaseModel):
    status: str
    scrape_run_id: int


class ScrapeRunStatus(BaseModel):
    status: str
    jobs_found: int
    jobs_created: int
    jobs_updated: int
    jobs_skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    parsed_jobs: list[ParsedJobSummary] = Field(default_factory=list)


class ScrapeRunBase(BaseModel):
    source_id: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    jobs_found: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_skipped: int = 0
    error_message: str | None = None


class ScrapeRunCreate(ScrapeRunBase):
    pass


class ScrapeRunRead(ScrapeRunBase, ORMModel):
    id: int


class RawJobSnapshotBase(BaseModel):
    source_id: int
    source_job_id: str | None = None
    url: str
    raw_html: str | None = None
    raw_text: str | None = None
    raw_json: dict[str, Any] | None = None
    content_hash: str | None = None


class RawJobSnapshotCreate(RawJobSnapshotBase):
    pass


class RawJobSnapshotRead(RawJobSnapshotBase, ORMModel):
    id: int
    scraped_at: datetime


class CompanyBase(BaseModel):
    name: str
    website: str | None = None
    sector: str | None = None
    size_band: str | None = None


class CompanyCreate(CompanyBase):
    pass


class CompanyRead(CompanyBase, ORMModel):
    id: int


class JobBase(BaseModel):
    source_id: int
    source_job_id: str
    canonical_url: str
    title: str
    company_name: str | None = None
    location: str | None = None
    remote_type: str | None = None
    employment_type: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_min_raw: Decimal | None = None
    salary_max_raw: Decimal | None = None
    salary_period: str | None = None
    normalized_annual_min: Decimal | None = None
    normalized_annual_max: Decimal | None = None
    description_text: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    status: str = "active"
    content_hash: str | None = None


class JobCreate(JobBase):
    pass


class JobRead(JobBase, ORMModel):
    id: int
    first_seen_at: datetime
    last_seen_at: datetime


class JobListItem(BaseModel):
    id: int
    title: str
    company_name: str | None = None
    location: str | None = None
    remote_type: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_min_raw: Decimal | None = None
    salary_max_raw: Decimal | None = None
    salary_period: str | None = None
    normalized_annual_min: Decimal | None = None
    normalized_annual_max: Decimal | None = None
    posted_at: datetime | None = None
    role_family: str | None = None
    recommendation_tier: str | None = None
    total_score: Decimal | None = None
    recommendation: str | None = None
    matched_skills_count: int
    missing_skills_count: int
    status: str


class PaginatedJobs(BaseModel):
    items: list[JobListItem]
    page: int
    page_size: int
    total_count: int
    total_pages: int


class JobIdsRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1)


class BulkJobActionResult(BaseModel):
    affected: int
    job_ids: list[int]


class JobCompanyRead(ORMModel):
    job_id: int
    company_id: int


class JobSkillBase(BaseModel):
    job_id: int
    skill_name: str
    skill_category: str | None = None
    importance: str | None = None
    evidence_text: str | None = None


class JobSkillCreate(JobSkillBase):
    pass


class JobSkillRead(JobSkillBase, ORMModel):
    id: int


class JobAnalysisBase(BaseModel):
    job_id: int
    seniority_level: str | None = None
    role_family: str | None = None
    role_focus: str | None = None
    tools_detected: list[str] | None = None
    responsibilities: list[str] | None = None
    requirements: list[str] | None = None
    nice_to_haves: list[str] | None = None
    red_flags: list[str] | None = None


class JobAnalysisCreate(JobAnalysisBase):
    pass


class JobAnalysisRead(JobAnalysisBase, ORMModel):
    id: int
    analysed_at: datetime


class JobScoreBase(BaseModel):
    job_id: int
    user_id: int
    total_score: Decimal
    role_match_score: Decimal | None = None
    skill_match_score: Decimal | None = None
    experience_score: Decimal | None = None
    salary_score: Decimal | None = None
    location_score: Decimal | None = None
    freshness_score: Decimal | None = None
    missing_skill_penalty: Decimal | None = None
    explanation: str | None = None
    recommendation_tier: str | None = None


class JobScoreCreate(JobScoreBase):
    pass


class JobScoreRead(JobScoreBase, ORMModel):
    id: int
    scored_at: datetime


class JobScorecard(BaseModel):
    job_id: int
    total_score: Decimal | float
    tier: str
    recommendation: str
    confidence_score: Decimal | float
    score_breakdown: dict[str, Decimal | float]
    matched_skills: list[str]
    missing_skills: list[str]
    matched_evidence: list[str]
    risks: list[str]
    gates: list[str]
    why: str


class MissingSkillBase(BaseModel):
    job_id: int
    user_id: int
    skill_name: str
    importance: str | None = None
    learning_priority: str | None = None
    evidence_text: str | None = None


class MissingSkillCreate(MissingSkillBase):
    pass


class MissingSkillRead(MissingSkillBase, ORMModel):
    id: int


class SavedJobBase(BaseModel):
    user_id: int
    job_id: int
    status: str = "saved"
    notes: str | None = None


class SavedJobCreate(SavedJobBase):
    pass


class SavedJobUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


class SavedJobRead(SavedJobBase, ORMModel):
    id: int
    saved_at: datetime


class SavedJobListItem(SavedJobRead):
    job: JobListItem | None = None


class JobDetail(BaseModel):
    job: JobRead
    analysis: JobAnalysisRead | None = None
    score: JobScoreRead | None = None
    matched_skills: list[JobSkillRead]
    missing_skills: list[MissingSkillRead]
    red_flags: list[str]
    saved_status: str | None = None


class AnalyticsOverview(BaseModel):
    total_jobs: int
    analysed_jobs: int
    scored_jobs: int
    saved_jobs: int
    applied_jobs: int
    excellent_matches: int
    strong_matches: int
    stretch_roles: int
    excluded_jobs: int
    average_score: Decimal | None = None
    newest_job_date: datetime | None = None


class RoleFitItem(BaseModel):
    role_family: str | None
    count: int
    average_score: Decimal | None = None
    recommendation_tiers: dict[str, int]


class RoleFitAnalytics(BaseModel):
    items: list[RoleFitItem]


class SkillGapItem(BaseModel):
    skill_name: str
    count: int
    highest_priority: str | None = None


class SkillGapAnalytics(BaseModel):
    missing_skill_frequency: list[SkillGapItem]
    high_priority_missing_skills: list[SkillGapItem]
    skills_linked_to_most_jobs: list[SkillGapItem]
    top_10_learning_priorities: list[SkillGapItem]


class SalaryGroup(BaseModel):
    group: str | None
    average_salary_min: Decimal | None = None
    average_salary_max: Decimal | None = None
    count: int


class SalaryAnalytics(BaseModel):
    average_salary_min: Decimal | None = None
    average_salary_max: Decimal | None = None
    salary_by_role_family: list[SalaryGroup]
    salary_by_remote_type: list[SalaryGroup]
    missing_salary_count: int


class SourceHealthItem(BaseModel):
    source_id: int
    source_name: str
    jobs_count: int
    last_scrape_run_id: int | None = None
    last_scrape_started_at: datetime | None = None
    last_scrape_finished_at: datetime | None = None
    scrape_status: str | None = None
    jobs_found: int | None = None
    jobs_created: int | None = None
    jobs_updated: int | None = None
    error_message: str | None = None


class SourceHealthAnalytics(BaseModel):
    items: list[SourceHealthItem]


class JobEventBase(BaseModel):
    job_id: int
    event_type: str
    event_data: dict[str, Any] | None = None


class JobEventCreate(JobEventBase):
    pass


class JobEventRead(JobEventBase, ORMModel):
    id: int
    created_at: datetime
