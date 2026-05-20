import type {
  AIChatResponse,
  AnalyticsOverview,
  JobDetail,
  JobScorecard,
  JobsRescoreResult,
  PaginatedJobs,
  RoleFitAnalytics,
  SalaryAnalytics,
  SavedJob,
  SkillGapAnalytics,
  ScrapeNowPayload,
  ScrapeRunStatus,
  ScrapeStartResult,
  Source,
  SourceFromUrlPayload,
  SourceHealthAnalytics,
  SourceTestResult,
  UserProfile
} from "@/types/api";

const PRODUCTION_API_BASE_URL = "https://job-intelligence-ai-63rj.onrender.com";
const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
const API_BASE_URL =
  configuredApiBaseUrl ?? (process.env.NODE_ENV === "production" ? PRODUCTION_API_BASE_URL : "http://127.0.0.1:8000");
const REQUEST_TIMEOUT_MS = 10000;

export const apiConfig = {
  baseUrl: API_BASE_URL,
  envVarConfigured: Boolean(configuredApiBaseUrl),
  fallbackUsed: !configuredApiBaseUrl,
  nodeEnv: process.env.NODE_ENV
};

if (typeof window !== "undefined") {
  console.info("Job Intelligence API config", apiConfig);
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError("NEXT_PUBLIC_API_BASE_URL is required in production", 0);
  }
  const url = `${API_BASE_URL}${path}`;
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      ...init,
      headers,
      cache: "no-store",
      signal: controller.signal
    });

    if (!response.ok) {
      let detail = `Request failed with status ${response.status}`;
      try {
        const body = await response.json();
        detail = typeof body.detail === "string" ? body.detail : detail;
      } catch {
        // Keep the generic status message.
      }
      const error = new ApiError(detail, response.status);
      console.error("API request failed", { url, status: response.status, detail });
      throw error;
    }

    return response.json() as Promise<T>;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      const timeoutError = new ApiError(`Request timed out after ${REQUEST_TIMEOUT_MS / 1000}s: ${url}`, 0);
      console.error("API request timed out", { url, timeoutMs: REQUEST_TIMEOUT_MS });
      throw timeoutError;
    }
    console.error("API request failed", { url, error });
    throw error instanceof Error ? error : new ApiError("Unknown API request failure", 0);
  } finally {
    globalThis.clearTimeout(timeoutId);
  }
}

export const api = {
  overview: () => request<AnalyticsOverview>("/analytics/overview"),
  jobs: (params: URLSearchParams) => request<PaginatedJobs>(`/jobs?${params.toString()}`),
  jobDetail: (id: string | number) => request<JobDetail>(`/jobs/${id}`),
  rescoreJobs: () => request<JobsRescoreResult>("/jobs/rescore", { method: "POST" }),
  jobScorecard: (id: string | number) => request<JobScorecard>(`/jobs/${id}/scorecard`),
  deleteJob: (id: string | number) => request<{ affected: number; job_ids: number[] }>(`/jobs/${id}`, { method: "DELETE" }),
  bulkDeleteJobs: (jobIds: number[]) =>
    request<{ affected: number; job_ids: number[] }>("/jobs/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds })
    }),
  bulkExcludeJobs: (jobIds: number[]) =>
    request<{ affected: number; job_ids: number[] }>("/jobs/bulk-exclude", {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds })
    }),
  saveJob: (id: string | number) => request<SavedJob>(`/jobs/${id}/save`, { method: "POST" }),
  rejectJob: (id: string | number) => request<SavedJob>(`/jobs/${id}/reject`, { method: "POST" }),
  markApplied: (id: string | number) => request<SavedJob>(`/jobs/${id}/mark-applied`, { method: "POST" }),
  savedJobs: () => request<SavedJob[]>("/saved-jobs"),
  updateSavedJob: (id: number, body: { status?: string; notes?: string | null }) =>
    request<SavedJob>(`/saved-jobs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  skillGaps: () => request<SkillGapAnalytics>("/analytics/skill-gaps"),
  roleFit: () => request<RoleFitAnalytics>("/analytics/role-fit"),
  salary: () => request<SalaryAnalytics>("/analytics/salary"),
  sourceHealth: () => request<SourceHealthAnalytics>("/analytics/source-health"),
  sources: () => request<Source[]>("/sources"),
  createSourceFromUrl: (body: SourceFromUrlPayload) =>
    request<Source>("/sources/from-url", { method: "POST", body: JSON.stringify(body) }),
  testSourceUrl: (id: number, targetUrl?: string) =>
    request<SourceTestResult>(`/sources/${id}/test-url`, {
      method: "POST",
      body: JSON.stringify({ target_url: targetUrl || null })
    }),
  scrapeSourceNow: (id: number, body: ScrapeNowPayload) =>
    request<ScrapeStartResult>(`/sources/${id}/scrape-now`, { method: "POST", body: JSON.stringify(body) }),
  scrapeRun: (id: number) => request<ScrapeRunStatus>(`/scrape-runs/${id}`),
  profile: () => request<UserProfile | null>("/profile"),
  saveProfileCv: (cvText: string) =>
    request<UserProfile>("/profile/cv", { method: "POST", body: JSON.stringify({ cv_text: cvText }) }),
  aiChat: (message: string) =>
    request<AIChatResponse>("/ai/chat", { method: "POST", body: JSON.stringify({ message }) })
};
