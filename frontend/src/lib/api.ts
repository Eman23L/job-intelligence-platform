import type {
  AIChatResponse,
  AIHistoryMessage,
  Application,
  ApplicationCreatePayload,
  ApplicationStatus,
  ApplicationsList,
  ApplyPack,
  AnalyticsOverview,
  JobDetail,
  JobAvailabilityResult,
  JobAvailabilityRunStart,
  JobAvailabilityRunStatus,
  JobApplyStrategyRunStart,
  JobApplyStrategyRunStatus,
  JobRescoreRunStart,
  JobRescoreRunStatus,
  JobServeSearchPayload,
  JobScorecard,
  PaginatedJobs,
  RoleFitAnalytics,
  SalaryAnalytics,
  SavedJob,
  SkillGapAnalytics,
  ScrapeNowPayload,
  ScrapeRunStatus,
  ScrapeStartResult,
  Source,
  SourceDeleteResult,
  SourceFromUrlPayload,
  SourceHealthAnalytics,
  SourceScrapeRunStart,
  SourceScrapeRunStatus,
  SourceTestResult,
  UnifiedRun,
  UnifiedRunList,
  UserProfile
} from "@/types/api";
import { clearToken, getToken, redirectToLogin } from "@/lib/session";

const PRODUCTION_API_BASE_URL = "https://job-intelligence-ai-63rj.onrender.com";
const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
const API_BASE_URL =
  configuredApiBaseUrl ?? (process.env.NODE_ENV === "production" ? PRODUCTION_API_BASE_URL : "http://127.0.0.1:8000");
const REQUEST_TIMEOUT_MS = 10000;
const DASHBOARD_REQUEST_TIMEOUT_MS = 20000;
const JOBS_REQUEST_TIMEOUT_MS = 20000;
const SOURCES_REQUEST_TIMEOUT_MS = 20000;

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
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError("NEXT_PUBLIC_API_BASE_URL is required in production", 0);
  }
  const { timeoutMs: configuredTimeoutMs, ...fetchInit } = init ?? {};
  const timeoutMs = configuredTimeoutMs ?? REQUEST_TIMEOUT_MS;
  const url = `${API_BASE_URL}${path}`;
  const headers = new Headers(fetchInit.headers);
  if (fetchInit.body && !(fetchInit.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...fetchInit,
      headers,
      cache: "no-store",
      signal: controller.signal
    });

    if (response.status === 401 && !path.startsWith("/auth/")) {
      // Session expired or revoked: send the user back to sign in.
      clearToken();
      redirectToLogin();
    }

    if (!response.ok) {
      let detail = `Request failed with status ${response.status}`;
      let code: string | undefined;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") {
          detail = body.detail;
        } else if (body.detail && typeof body.detail === "object") {
          code = typeof body.detail.error === "string" ? body.detail.error : undefined;
          detail = typeof body.detail.message === "string" ? body.detail.message : detail;
        }
      } catch {
        // Keep the generic status message.
      }
      const error = new ApiError(detail, response.status, code);
      console.error("API request failed", { url, status: response.status, detail, code });
      throw error;
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return response.json() as Promise<T>;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      const timeoutError = new ApiError(`Request timed out after ${timeoutMs / 1000}s`, 0);
      console.error("API request timed out", { url, timeoutMs });
      throw timeoutError;
    }
    console.error("API request failed", { url, error });
    throw error instanceof Error ? error : new ApiError("Unknown API request failure", 0);
  } finally {
    globalThis.clearTimeout(timeoutId);
  }
}

// Fetch a file with the signed-in token and hand it to the browser as a download.
async function downloadFile(path: string): Promise<string> {
  const token = getToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    cache: "no-store"
  });
  if (!response.ok) {
    let detail = `Download failed with status ${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      // Keep the generic message.
    }
    throw new ApiError(detail, response.status);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "cv";
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return filename;
}

export interface Account {
  id: number;
  email: string;
  display_name: string | null;
  is_admin: boolean;
}

export interface SessionResponse {
  token: string;
  user: Account;
}

export const authApi = {
  status: () => request<{ setup_required: boolean }>("/auth/status", { timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS }),
  login: (email: string, password: string) =>
    request<SessionResponse>("/auth/login", {
      method: "POST",
      timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS,
      body: JSON.stringify({ email, password })
    }),
  setup: (body: { setup_code: string; email: string; password: string; name: string }) =>
    request<SessionResponse>("/auth/setup", { method: "POST", timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS, body: JSON.stringify(body) }),
  register: (body: { invite_code: string; email: string; password: string; name: string }) =>
    request<SessionResponse>("/auth/register", { method: "POST", timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS, body: JSON.stringify(body) }),
  me: () => request<Account>("/auth/me", { timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  createInvite: (note?: string) =>
    request<{ code: string; expires_at: string }>("/auth/invites", { method: "POST", body: JSON.stringify({ note: note ?? null }) })
};

export const api = {
  overview: () => request<AnalyticsOverview>("/analytics/overview", { timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS }),
  applications: () => request<ApplicationsList>("/applications"),
  prepareApplications: (minimumScore?: number) =>
    request<{ added: number; minimum_apply_score: number }>(
      `/applications/prepare${minimumScore !== undefined ? `?minimum_score=${minimumScore}` : ""}`,
      { method: "POST", timeoutMs: JOBS_REQUEST_TIMEOUT_MS }
    ),
  createApplication: (body: ApplicationCreatePayload) =>
    request<Application>("/applications", { method: "POST", body: JSON.stringify(body) }),
  updateApplication: (id: number, body: { status?: ApplicationStatus; notes?: string }) =>
    request<Application>(`/applications/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteApplication: (id: number) => request<void>(`/applications/${id}`, { method: "DELETE" }),
  applyPack: (id: number | string) => request<ApplyPack>(`/applications/${id}/pack`),
  downloadCv: () => downloadFile("/profile/cv-file"),
  jobs: (params: URLSearchParams) => request<PaginatedJobs>(`/jobs?${params.toString()}`, { timeoutMs: JOBS_REQUEST_TIMEOUT_MS }),
  jobDetail: (id: string | number) => request<JobDetail>(`/jobs/${id}`),
  rescoreJobs: () => request<JobRescoreRunStart>("/jobs/rescore", { method: "POST" }),
  rescoreRun: (id: number) => request<JobRescoreRunStatus>(`/jobs/rescore-runs/${id}`),
  retryRescoreRun: (id: number) => request<JobRescoreRunStart>(`/jobs/rescore-runs/${id}/retry`, { method: "POST" }),
  cancelRescoreRun: (id: number) => request<JobRescoreRunStart>(`/jobs/rescore-runs/${id}/cancel`, { method: "POST" }),
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
  checkJobAvailability: (id: string | number) =>
    request<JobAvailabilityResult>(`/jobs/${id}/check-availability`, { method: "POST", timeoutMs: JOBS_REQUEST_TIMEOUT_MS }),
  checkJobsAvailability: (jobIds?: number[]) =>
    request<JobAvailabilityRunStart>("/jobs/check-availability", {
      method: "POST",
      timeoutMs: JOBS_REQUEST_TIMEOUT_MS,
      body: JSON.stringify({ job_ids: jobIds ?? null })
    }),
  availabilityRun: (id: number) => request<JobAvailabilityRunStatus>(`/jobs/availability-runs/${id}`),
  checkListingsOpen: () => request<JobAvailabilityRunStart>("/applications/check-open", { method: "POST" }),
  skipClosedListings: () => request<{ skipped: number }>("/applications/skip-closed", { method: "POST" }),
  classifyApplyStrategies: (jobIds?: number[]) =>
    request<JobApplyStrategyRunStart>("/jobs/classify-apply-strategies", {
      method: "POST",
      timeoutMs: JOBS_REQUEST_TIMEOUT_MS,
      body: jobIds ? JSON.stringify({ job_ids: jobIds }) : undefined
    }),
  applyStrategyRun: (id: number) => request<JobApplyStrategyRunStatus>(`/jobs/apply-strategy-runs/${id}`),
  saveJob: (id: string | number) => request<SavedJob>(`/jobs/${id}/save`, { method: "POST" }),
  rejectJob: (id: string | number) => request<SavedJob>(`/jobs/${id}/reject`, { method: "POST" }),
  addToApplyList: (id: string | number) => request<Application>(`/jobs/${id}/add-to-apply-list`, { method: "POST" }),
  markApplied: (id: string | number) => request<Application>(`/jobs/${id}/mark-applied`, { method: "POST" }),
  markSkipped: (id: string | number) => request<Application>(`/jobs/${id}/mark-skipped`, { method: "POST" }),
  savedJobs: () => request<SavedJob[]>("/saved-jobs"),
  updateSavedJob: (id: number, body: { status?: string; notes?: string | null }) =>
    request<SavedJob>(`/saved-jobs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  skillGaps: () => request<SkillGapAnalytics>("/analytics/skill-gaps"),
  roleFit: () => request<RoleFitAnalytics>("/analytics/role-fit"),
  salary: () => request<SalaryAnalytics>("/analytics/salary"),
  sourceHealth: () => request<SourceHealthAnalytics>("/analytics/source-health", { timeoutMs: SOURCES_REQUEST_TIMEOUT_MS }),
  sources: () => request<Source[]>("/sources", { timeoutMs: SOURCES_REQUEST_TIMEOUT_MS }),
  createSourceFromUrl: (body: SourceFromUrlPayload) =>
    request<Source>("/sources/from-url", { method: "POST", body: JSON.stringify(body) }),
  testSourceUrl: (id: number, targetUrl?: string) =>
    request<SourceTestResult>(`/sources/${id}/test-url`, {
      method: "POST",
      body: JSON.stringify({ target_url: targetUrl || null })
    }),
  scrapeSourceNow: (id: number, body: ScrapeNowPayload) =>
    request<ScrapeStartResult>(`/sources/${id}/scrape-now`, { method: "POST", body: JSON.stringify(body) }),
  searchScrapeJobServe: (body: JobServeSearchPayload) =>
    request<SourceScrapeRunStart>("/sources/jobserve/search-scrape", {
      method: "POST",
      timeoutMs: SOURCES_REQUEST_TIMEOUT_MS,
      body: JSON.stringify(body)
    }),
  sourceScrapeRun: (id: number) => request<SourceScrapeRunStatus>(`/sources/scrape-runs/${id}`),
  deleteSource: (id: number, deleteJobs = false) =>
    request<SourceDeleteResult>(`/sources/${id}?delete_jobs=${deleteJobs ? "true" : "false"}`, { method: "DELETE" }),
  scrapeRun: (id: number) => request<ScrapeRunStatus>(`/scrape-runs/${id}`),
  runs: (params: URLSearchParams) => request<UnifiedRunList>(`/runs?${params.toString()}`),
  retryRun: (type: string, id: string | number) => request<UnifiedRun>(`/runs/${type}/${id}/retry`, { method: "POST" }),
  cancelRun: (type: string, id: string | number) => request<UnifiedRun>(`/runs/${type}/${id}/cancel`, { method: "POST" }),
  profile: () => request<UserProfile | null>("/profile"),
  saveProfileCv: (cvText: string) =>
    request<UserProfile>("/profile/cv", { method: "POST", body: JSON.stringify({ cv_text: cvText }) }),
  saveApplicationProfile: (body: Partial<UserProfile>) =>
    request<UserProfile>("/profile/application", { method: "POST", body: JSON.stringify(body) }),
  uploadProfileCvFile: (file: File) => {
    const formData = new FormData();
    formData.set("file", file);
    return request<UserProfile>("/profile/cv-file", { method: "POST", body: formData });
  },
  aiChat: (message: string) =>
    request<AIChatResponse>("/ai/chat", { method: "POST", body: JSON.stringify({ message }) }),
  aiHistory: () => request<AIHistoryMessage[]>("/ai/history"),
  clearAiHistory: () => request<{ deleted: number }>("/ai/history", { method: "DELETE" })
};
