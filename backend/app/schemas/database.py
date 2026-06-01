from __future__ import annotations

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


class ApplicationProfileUpdate(BaseModel):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    address: str | None = None
    country: str | None = None
    work_status_uk: str | None = None
    salary_expectation: str | None = None
    travel_distance: str | None = None
    availability_notice: str | None = None
    salary_expectation_gbp: int | None = None
    travel_distance_miles: int | None = None
    minimum_apply_score: int = Field(default=80, ge=0, le=100)
    sponsorship_required: bool = False


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
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    address: str | None = None
    country: str | None = None
    work_status_uk: str | None = None
    salary_expectation: str | None = None
    travel_distance: str | None = None
    availability_notice: str | None = None
    salary_expectation_gbp: int | None = None
    travel_distance_miles: int | None = None
    minimum_apply_score: int = 80
    sponsorship_required: bool = False
    cv_file_path: str | None = None
    cv_file_name: str | None = None
    cv_file_mime_type: str | None = None
    cv_file_size: int | None = None
    cv_uploaded_at: datetime | None = None
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


class JobServeSearchScrapeRequest(BaseModel):
    keywords: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    posted_within_days: int | None = Field(default=7, ge=1, le=30)
    distance: str = "Within 50 miles"
    select_all_industries: bool = True
    posted_within: str = "Within 7 days"
    job_type: str = "Any"
    remote_only: bool = False
    max_pages: int = Field(default=3, ge=1, le=10)
    check_availability_after: bool = False


class JobServeSearchScrapeResult(BaseModel):
    source_id: int
    search_url: str
    jobs_found: int
    jobs_created: int
    jobs_updated: int
    jobs_skipped: int
    parsed_jobs: list[ParsedJobSummary] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class SourceScrapeRunStart(BaseModel):
    run_id: int
    status: str


class SourceScrapeRunStatus(BaseModel):
    run_id: int
    status: str
    found: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    error: str | None = None
    search_params: dict[str, Any] = Field(default_factory=dict)
    final_search_url: str | None = None
    result_count: int = 0
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class SourceDeleteResult(BaseModel):
    source_id: int
    action: str
    deleted_jobs: bool = False


class UnifiedRun(BaseModel):
    id: str
    type: str
    status: str
    total: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    error: str | None = None
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


class UnifiedRunList(BaseModel):
    items: list[UnifiedRun]


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
    original_title: str | None = None
    original_company: str | None = None
    original_location: str | None = None
    original_salary: str | None = None
    original_external_id: str | None = None
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
    application_status: str = "not_started"
    availability_status: str = "unknown"
    last_checked_at: datetime | None = None
    availability_reason: str | None = None
    apply_strategy: str = "unknown"
    apply_difficulty: str = "unknown"
    apply_strategy_reason: str | None = None
    assisted_started_at: datetime | None = None
    assisted_result: dict[str, Any] | None = None
    assisted_warnings: list[str] | None = None
    last_apply_attempt_at: datetime | None = None
    applied_at: datetime | None = None
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
    application_status: str
    availability_status: str
    last_checked_at: datetime | None = None
    availability_reason: str | None = None
    apply_strategy: str
    apply_difficulty: str
    apply_strategy_reason: str | None = None
    apply_readiness_score: Decimal | None = None


class PaginatedJobs(BaseModel):
    items: list[JobListItem]
    page: int
    page_size: int
    total_count: int
    total_pages: int
    warning: str | None = None


class JobIdsRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1)


class BulkJobActionResult(BaseModel):
    affected: int
    job_ids: list[int]


class JobAvailabilityCheckRequest(BaseModel):
    job_ids: list[int] | None = None


class JobAvailabilityResult(BaseModel):
    job_id: int
    availability_status: str
    last_checked_at: datetime
    availability_reason: str | None = None
    final_url: str | None = None
    status_code: int | None = None


class BulkJobAvailabilityResult(BaseModel):
    checked: int
    results: list[JobAvailabilityResult]


class JobAvailabilityRunStart(BaseModel):
    run_id: int
    status: str


class JobAvailabilityRunStatus(BaseModel):
    run_id: int
    status: str
    total: int = 0
    processed: int = 0
    checked: int = 0
    failed: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


class JobApplyStrategyResult(BaseModel):
    job_id: int
    apply_strategy: str
    apply_difficulty: str
    apply_strategy_reason: str | None = None
    apply_readiness_score: Decimal | None = None


class JobApplyStrategyRunStart(BaseModel):
    run_id: int
    status: str


class JobApplyStrategyRunStatus(BaseModel):
    run_id: int
    status: str
    total: int = 0
    processed: int = 0
    classified: int = 0
    failed: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


class JobRescoreRunStart(BaseModel):
    run_id: int
    status: str


class JobRescoreRunStatus(BaseModel):
    run_id: int
    status: str
    total: int = 0
    scored: int = 0
    skipped: int = 0
    failed: int = 0
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    estimated_seconds_remaining: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    error: str | None = None


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
    apply_readiness_score: Decimal | None = None
    explanation: str | None = None
    recommendation: str | None = None
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


class ApplicationItem(BaseModel):
    job_id: int
    title: str
    company_name: str | None = None
    location: str | None = None
    apply_url: str
    application_status: str
    availability_status: str
    last_checked_at: datetime | None = None
    availability_reason: str | None = None
    apply_strategy: str
    apply_difficulty: str
    apply_strategy_reason: str | None = None
    apply_readiness_score: Decimal | None = None
    assisted_started_at: datetime | None = None
    assisted_result: dict[str, Any] | None = None
    assisted_warnings: list[str] | None = None
    last_apply_attempt_at: datetime | None = None
    total_score: Decimal | None = None
    recommendation_tier: str | None = None
    recommendation: str | None = None


class ApplicationsList(BaseModel):
    items: list[ApplicationItem]
    minimum_apply_score: int = 80
    warning: str | None = None


class AssistApplyResult(BaseModel):
    status: str
    filled_fields: list[str] = Field(default_factory=list)
    unfilled_fields: list[str] = Field(default_factory=list)
    unfilled_required_fields: list[str] = Field(default_factory=list)
    uploaded_cv: bool = False
    submitted: bool = False
    warnings: list[str] = Field(default_factory=list)
    screenshot_path: str | None = None
    screenshot_paths: list[str] = Field(default_factory=list)
    screenshot_urls: list[str] = Field(default_factory=list)
    html_snapshot_paths: list[str] = Field(default_factory=list)
    html_snapshot_urls: list[str] = Field(default_factory=list)
    detected_buttons: list[dict[str, Any]] = Field(default_factory=list)
    detected_fields: list[dict[str, Any]] = Field(default_factory=list)
    detected_selects: list[dict[str, Any]] = Field(default_factory=list)
    detected_iframes: list[dict[str, Any]] = Field(default_factory=list)
    debug_steps: list[dict[str, Any]] = Field(default_factory=list)
    final_url: str | None = None
    final_error: str | None = None
    running_step: str | None = None
    debug_mode: bool = False
    timing_diagnostics: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    profile_diagnostics: dict[str, Any] = Field(default_factory=dict)
    jobserve_flow_diagnostics: dict[str, Any] = Field(default_factory=dict)
    upload_diagnostics: dict[str, Any] = Field(default_factory=dict)
    select_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
    submitted_job_title: str | None = None
    submitted_job_company: str | None = None
    submitted_job_reference: str | None = None
    submitted_job_external_id: str | None = None
    applied_at: datetime | None = None
    confirmation_text: str | None = None
    registration_toggle_disabled: bool = False
    modal_closed: bool = False


class AssistApplyRequest(BaseModel):
    mode: str = "review_only"
    debug_mode: bool = False


class AssistApplyDiagnosticRun(BaseModel):
    run_id: str
    status: str
    latest_progress: dict[str, Any] = Field(default_factory=dict)
    final_report: dict[str, Any] | None = None
    markdown_summary: str | None = None
    artifact_links: list[str] = Field(default_factory=list)


class AutonomousRealSubmitStatus(BaseModel):
    enabled: bool
    max_submits_per_run: int
    eligible_application_ids: list[int] = Field(default_factory=list)
    last_result: dict[str, Any] | None = None


class AutonomousRealSubmitRunResult(BaseModel):
    status: str
    application_id: int | None = None
    submitted: bool = False
    failed_phase: str | None = None
    exact_error: str | None = None
    recommended_fix: str
    diagnostic_run_id: str | None = None
    codex_handoff_status: str | None = None
    github_issue_url: str | None = None
    codex_handoff_error: str | None = None
    codex_handoff_attempt_count: int | None = None
    focused_application_id: int | None = None
    attempt_number: int = 0
    max_attempts: int = 3
    waiting_for_fix_deploy: bool = False
    will_retry_same_application: bool = False
    will_move_to_next_application: bool = False
    orchestration_steps: list[dict[str, Any]] = Field(default_factory=list)
    current_url: str | None = None
    page_title: str | None = None
    screenshot_paths: list[str] = Field(default_factory=list)
    screenshot_urls: list[str] = Field(default_factory=list)
    html_snapshot_paths: list[str] = Field(default_factory=list)
    html_snapshot_urls: list[str] = Field(default_factory=list)
    detected_buttons: list[dict[str, Any]] = Field(default_factory=list)
    detected_fields: list[dict[str, Any]] = Field(default_factory=list)
    detected_selects: list[dict[str, Any]] = Field(default_factory=list)
    select_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    last_known_stage: str | None = None
    artifact_links: list[str] = Field(default_factory=list)
    traceback: str | None = None
    created_at: str


class BrowserStatus(BaseModel):
    service_type: str = "web"
    queue_enabled: bool
    redis_connected: bool
    playwright_installed: bool
    chromium_available: bool
    playwright_browsers_path: str | None = None
    chromium_executable_path: str | None = None
    chromium_file_exists: bool = False
    chromium_file_executable: bool = False
    worker_running: bool


class ApplicationsPrepareResult(BaseModel):
    queued: int
    job_ids: list[int]


class ApplicationPrepareRunStart(BaseModel):
    run_id: int
    status: str


class ApplicationPrepareRunStatus(BaseModel):
    run_id: int
    status: str
    total: int = 0
    processed: int = 0
    queued: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


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
