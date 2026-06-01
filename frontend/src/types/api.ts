export type Nullable<T> = T | null;

export interface AnalyticsOverview {
  total_jobs: number;
  analysed_jobs: number;
  scored_jobs: number;
  saved_jobs: number;
  applied_jobs: number;
  excellent_matches: number;
  strong_matches: number;
  stretch_roles: number;
  excluded_jobs: number;
  average_score: string | null;
  newest_job_date: string | null;
}

export interface JobListItem {
  id: number;
  title: string;
  company_name: string | null;
  location: string | null;
  remote_type: string | null;
  salary_min: string | null;
  salary_max: string | null;
  salary_currency: string | null;
  salary_min_raw: string | null;
  salary_max_raw: string | null;
  salary_period: string | null;
  normalized_annual_min: string | null;
  normalized_annual_max: string | null;
  posted_at: string | null;
  role_family: string | null;
  recommendation_tier: string | null;
  total_score: string | null;
  recommendation: string | null;
  matched_skills_count: number;
  missing_skills_count: number;
  status: string;
  application_status: ApplicationStatus;
  availability_status: AvailabilityStatus;
  last_checked_at: string | null;
  availability_reason: string | null;
  apply_strategy: string;
  apply_difficulty: ApplyDifficulty;
  apply_strategy_reason: string | null;
  apply_readiness_score: string | null;
}

export interface PaginatedJobs {
  items: JobListItem[];
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
  warning?: string | null;
}

export interface JobRecord extends JobListItem {
  source_id: number;
  source_job_id: string;
  canonical_url: string;
  original_title: string | null;
  original_company: string | null;
  original_location: string | null;
  original_salary: string | null;
  original_external_id: string | null;
  employment_type: string | null;
  description_text: string | null;
  expires_at: string | null;
  status: string;
  application_status: ApplicationStatus;
  content_hash: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface JobAnalysis {
  id: number;
  job_id: number;
  seniority_level: string | null;
  role_family: string | null;
  role_focus: string | null;
  tools_detected: string[] | null;
  responsibilities: string[] | null;
  requirements: string[] | null;
  nice_to_haves: string[] | null;
  red_flags: string[] | null;
  analysed_at: string;
}

export interface JobScore {
  id: number;
  job_id: number;
  user_id: number;
  total_score: string;
  role_match_score: string | null;
  skill_match_score: string | null;
  experience_score: string | null;
  salary_score: string | null;
  location_score: string | null;
  freshness_score: string | null;
  missing_skill_penalty: string | null;
  apply_readiness_score: string | null;
  explanation: string | null;
  recommendation: string | null;
  recommendation_tier: string | null;
  scored_at: string;
}

export interface JobScorecard {
  job_id: number;
  total_score: number | string;
  tier: string;
  recommendation: "apply" | "maybe" | "skip" | string;
  confidence_score: number | string;
  score_breakdown: Record<string, number | string>;
  matched_skills: string[];
  missing_skills: string[];
  matched_evidence: string[];
  risks: string[];
  gates: string[];
  why: string;
}

export interface JobRescoreRunStart {
  run_id: number;
  status: "queued" | "running" | "completed" | "failed" | "stalled" | "canceled" | string;
}

export interface JobRescoreRunStatus {
  run_id: number;
  status: "queued" | "running" | "completed" | "failed" | "stalled" | "canceled" | string;
  total: number;
  scored: number;
  skipped: number;
  failed: number;
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  estimated_seconds_remaining: number | null;
  started_at: string | null;
  finished_at: string | null;
  last_heartbeat_at: string | null;
  error: string | null;
}

export type ApplicationStatus = "not_started" | "ready_to_apply" | "opened" | "applied" | "skipped" | "failed";
export type AvailabilityStatus = "active" | "expired" | "unavailable" | "redirected" | "replaced" | "unknown";
export type ApplyDifficulty = "easy" | "medium" | "hard" | "blocked" | "unknown";

export interface JobAvailabilityResult {
  job_id: number;
  availability_status: AvailabilityStatus;
  last_checked_at: string;
  availability_reason: string | null;
  final_url: string | null;
  status_code: number | null;
}

export interface BulkJobAvailabilityResult {
  checked: number;
  results: JobAvailabilityResult[];
}

export interface JobAvailabilityRunStart {
  run_id: number;
  status: "running" | "completed" | "failed" | string;
}

export interface JobAvailabilityRunStatus {
  run_id: number;
  status: "running" | "completed" | "failed" | string;
  total: number;
  processed: number;
  checked: number;
  failed: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_heartbeat_at: string | null;
}

export interface JobApplyStrategyRunStart {
  run_id: number;
  status: "queued" | "running" | "completed" | "failed" | string;
}

export interface JobApplyStrategyRunStatus {
  run_id: number;
  status: "queued" | "running" | "completed" | "failed" | string;
  total: number;
  processed: number;
  classified: number;
  failed: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_heartbeat_at: string | null;
}

export interface ApplicationItem {
  job_id: number;
  title: string;
  company_name: string | null;
  location: string | null;
  apply_url: string;
  application_status: ApplicationStatus;
  availability_status: AvailabilityStatus;
  last_checked_at: string | null;
  availability_reason: string | null;
  apply_strategy: string;
  apply_difficulty: ApplyDifficulty;
  apply_strategy_reason: string | null;
  apply_readiness_score: string | null;
  assisted_started_at: string | null;
  assisted_result: AssistApplyResult | null;
  assisted_warnings: string[] | null;
  last_apply_attempt_at: string | null;
  total_score: string | null;
  recommendation_tier: string | null;
  recommendation: "apply" | "maybe" | "skip" | string | null;
}

export interface ApplicationsList {
  items: ApplicationItem[];
  minimum_apply_score: number;
}

export interface AssistApplyResult {
  status: "review_required" | string;
  filled_fields: string[];
  unfilled_fields: string[];
  unfilled_required_fields: string[];
  uploaded_cv: boolean;
  submitted: boolean;
  warnings: string[];
  screenshot_path: string | null;
  screenshot_paths: string[];
  screenshot_urls: string[];
  html_snapshot_paths: string[];
  html_snapshot_urls: string[];
  detected_buttons: Array<Record<string, unknown>>;
  detected_fields: Array<Record<string, unknown>>;
  detected_selects: Array<Record<string, unknown>>;
  detected_iframes: Array<Record<string, unknown>>;
  debug_steps: Array<Record<string, unknown>>;
  final_url: string | null;
  final_error: string | null;
  running_step?: string | null;
  debug_mode: boolean;
  timing_diagnostics?: Record<string, unknown>;
  progress?: Record<string, unknown>;
  profile_diagnostics?: Record<string, unknown>;
  jobserve_flow_diagnostics?: Record<string, unknown>;
  upload_diagnostics?: Record<string, unknown>;
  select_diagnostics?: Array<Record<string, unknown>>;
  exceptions?: Array<Record<string, unknown>>;
  submitted_job_title?: string | null;
  submitted_job_company?: string | null;
  submitted_job_reference?: string | null;
  submitted_job_external_id?: string | null;
  applied_at?: string | null;
  confirmation_text?: string | null;
  registration_toggle_disabled?: boolean;
  modal_closed?: boolean;
}

export interface AssistApplyDiagnosticRun {
  run_id: string;
  status: "queued" | "running" | "passed" | "failed" | string;
  latest_progress: Record<string, unknown>;
  final_report: Record<string, unknown> | null;
  markdown_summary: string | null;
  artifact_links: string[];
}

export interface AutonomousRealSubmitStatus {
  enabled: boolean;
  max_submits_per_run: number;
  eligible_application_ids: number[];
  last_result: Record<string, unknown> | null;
}

export interface AutonomousRealSubmitRunResult {
  status: string;
  application_id: number | null;
  submitted: boolean;
  failed_phase: string | null;
  exact_error: string | null;
  recommended_fix: string;
  diagnostic_run_id: string | null;
  codex_handoff_status?: string | null;
  github_issue_url?: string | null;
  codex_handoff_error?: string | null;
  codex_handoff_attempt_count?: number | null;
  orchestration_steps: Array<Record<string, unknown>>;
  created_at: string;
}

export interface ApplicationsPrepareResult {
  queued: number;
  job_ids: number[];
}

export interface ApplicationPrepareRunStart {
  run_id: number;
  status: "running" | "completed" | "failed" | string;
}

export interface ApplicationPrepareRunStatus {
  run_id: number;
  status: "running" | "completed" | "failed" | string;
  total: number;
  processed: number;
  queued: number;
  skipped: number;
  failed: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_heartbeat_at: string | null;
}

export interface JobSkill {
  id: number;
  job_id: number;
  skill_name: string;
  skill_category: string | null;
  importance: string | null;
  evidence_text: string | null;
}

export interface MissingSkill {
  id: number;
  job_id: number;
  user_id: number;
  skill_name: string;
  importance: string | null;
  learning_priority: string | null;
  evidence_text: string | null;
}

export interface JobDetail {
  job: JobRecord;
  analysis: JobAnalysis | null;
  score: JobScore | null;
  matched_skills: JobSkill[];
  missing_skills: MissingSkill[];
  red_flags: string[];
  saved_status: string | null;
}

export interface SavedJob {
  id: number;
  user_id: number;
  job_id: number;
  status: string;
  notes: string | null;
  saved_at: string;
  job?: JobListItem | null;
}

export interface UserProfile {
  id: number;
  user_id: number;
  cv_text: string;
  summary: string;
  skills: string[];
  experience: string[];
  projects: string[];
  education: string[];
  preferred_roles: string[];
  preferences: {
    remote: string;
    location: string;
    salary: string;
    work_authorization?: string;
    target_seniority?: "junior" | "mid" | "mid_senior" | "senior" | "any" | string;
  };
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  address: string | null;
  country: string | null;
  work_status_uk: string | null;
  salary_expectation: string | null;
  travel_distance: string | null;
  availability_notice: string | null;
  salary_expectation_gbp: number | null;
  travel_distance_miles: number | null;
  minimum_apply_score: number;
  sponsorship_required: boolean;
  cv_file_path: string | null;
  cv_file_name: string | null;
  cv_uploaded_at: string | null;
  location_preference: string | null;
  remote_preference: string | null;
  salary_min_preference: string | null;
  salary_max_preference: string | null;
  created_at: string;
  updated_at: string;
}

export interface AIChatResponse {
  provider: string;
  model: string;
  response: string;
}

export interface AIHistoryMessage {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface SkillGapItem {
  skill_name: string;
  count: number;
  highest_priority: string | null;
}

export interface SkillGapAnalytics {
  missing_skill_frequency: SkillGapItem[];
  high_priority_missing_skills: SkillGapItem[];
  skills_linked_to_most_jobs: SkillGapItem[];
  top_10_learning_priorities: SkillGapItem[];
}

export interface RoleFitItem {
  role_family: string | null;
  count: number;
  average_score: string | null;
  recommendation_tiers: Record<string, number>;
}

export interface RoleFitAnalytics {
  items: RoleFitItem[];
}

export interface SalaryGroup {
  group: string | null;
  average_salary_min: string | null;
  average_salary_max: string | null;
  count: number;
}

export interface SalaryAnalytics {
  average_salary_min: string | null;
  average_salary_max: string | null;
  salary_by_role_family: SalaryGroup[];
  salary_by_remote_type: SalaryGroup[];
  missing_salary_count: number;
}

export interface SourceHealthItem {
  source_id: number;
  source_name: string;
  jobs_count: number;
  last_scrape_run_id: number | null;
  last_scrape_started_at: string | null;
  last_scrape_finished_at: string | null;
  scrape_status: string | null;
  jobs_found: number | null;
  jobs_created: number | null;
  jobs_updated: number | null;
  error_message: string | null;
}

export interface SourceHealthAnalytics {
  items: SourceHealthItem[];
}

export interface Source {
  id: number;
  name: string;
  base_url: string;
  source_type: string;
  robots_url: string | null;
  terms_url: string | null;
  scraping_allowed: boolean;
  permission_notes: string | null;
  rate_limit_per_minute: number;
  allowed_path_patterns: string[] | null;
  job_link_patterns: string[] | null;
  enabled: boolean;
  last_reviewed_at: string | null;
  created_at: string;
}

export interface SourceFromUrlPayload {
  name: string;
  base_url: string;
  source_type: string;
  permission_notes: string;
  scraping_allowed: boolean;
  rate_limit_per_minute: number;
  allowed_path_patterns?: string[] | null;
  job_link_patterns?: string[] | null;
}

export interface SourceTestResult {
  can_fetch: boolean;
  status_code: number | null;
  page_title: string | null;
  links_found_count: number;
  likely_job_links_count: number;
  sample_job_links: string[];
  discovered_job_ids: string[];
  warnings: string[];
  errors: string[];
}

export interface ScrapeNowPayload {
  start_url?: string | null;
  max_pages?: number;
  max_jobs?: number;
  delay_seconds?: number;
  dry_run?: boolean;
}

export interface ScrapeStartResult {
  status: "started";
  scrape_run_id: number;
}

export interface ScrapeRunStatus {
  status: "pending" | "running" | "completed" | "failed";
  scrape_run_id?: number | null;
  jobs_found: number;
  jobs_created: number;
  jobs_updated: number;
  jobs_skipped: number;
  parsed_jobs: Array<{
    source_job_id: string | null;
    title: string | null;
    company_name: string | null;
    location: string | null;
    canonical_url: string | null;
  }>;
  errors: string[];
}

export type ScrapeNowResult = ScrapeRunStatus;

export interface JobServeSearchPayload {
  keywords: string;
  location?: string | null;
  distance?: string;
  select_all_industries?: boolean;
  posted_within?: string;
  posted_within_days?: number | null;
  job_type?: string;
  remote_only?: boolean;
  max_pages?: number;
}

export interface SourceScrapeRunStart {
  run_id: number;
  status: "running" | "completed" | "failed" | string;
}

export interface SourceScrapeRunStatus {
  run_id: number;
  status: "running" | "completed" | "failed" | string;
  found: number;
  created: number;
  updated: number;
  skipped: number;
  error: string | null;
  search_params?: Record<string, unknown>;
  final_search_url?: string | null;
  result_count?: number;
  diagnostics?: Record<string, unknown>;
}

export interface SourceDeleteResult {
  source_id: number;
  action: string;
  deleted_jobs: boolean;
}

export interface UnifiedRun {
  id: string;
  type: "scrape" | "rescore" | "availability" | "application_prepare" | "apply_strategy" | string;
  status: string;
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  error: string | null;
  started_at: string | null;
  last_heartbeat_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface UnifiedRunList {
  items: UnifiedRun[];
}
