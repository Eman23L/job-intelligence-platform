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
  matched_skills_count: number;
  missing_skills_count: number;
}

export interface PaginatedJobs {
  items: JobListItem[];
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
}

export interface JobRecord extends JobListItem {
  source_id: number;
  source_job_id: string;
  canonical_url: string;
  employment_type: string | null;
  description_text: string | null;
  expires_at: string | null;
  status: string;
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
  explanation: string | null;
  recommendation_tier: string | null;
  scored_at: string;
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
