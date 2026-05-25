"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FiltersBar, type JobFiltersState } from "@/components/FiltersBar";
import { JobsTable } from "@/components/JobsTable";
import { LoadingState } from "@/components/LoadingState";
import { PaginationControls } from "@/components/PaginationControls";
import { ApiError, api } from "@/lib/api";
import type { JobAvailabilityRunStatus, JobRescoreRunStatus, JobScorecard, PaginatedJobs } from "@/types/api";

const initialFilters: JobFiltersState = {
  role_family: "",
  recommendation_tier: "",
  remote_type: "",
  location: "",
  company_name: "",
  min_score: "",
  max_score: "",
  exclude_excluded: false,
  availability_status: "",
  source_id: "",
  sort: "total_score_desc"
};

export default function JobsPage() {
  const [filters, setFilters] = useState<JobFiltersState>({
    ...initialFilters,
    source_id: typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("source_id") ?? ""
  });
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PaginatedJobs | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [rescoreRunId, setRescoreRunId] = useState<number | null>(null);
  const [rescoreRun, setRescoreRun] = useState<JobRescoreRunStatus | null>(null);
  const [rescorePollFailures, setRescorePollFailures] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "info" | "success" | "warning" | "error"; message: string } | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [scorecard, setScorecard] = useState<JobScorecard | null>(null);
  const [scorecardLoading, setScorecardLoading] = useState(false);
  const [checkingAvailability, setCheckingAvailability] = useState(false);
  const [availabilityRunId, setAvailabilityRunId] = useState<number | null>(null);
  const [availabilityRun, setAvailabilityRun] = useState<JobAvailabilityRunStatus | null>(null);
  const [availabilityPollFailures, setAvailabilityPollFailures] = useState(0);

  const params = useMemo(() => {
    const next = new URLSearchParams();
    next.set("page", String(page));
    next.set("page_size", "20");
    next.set("sort", filters.sort);
    Object.entries(filters).forEach(([key, value]) => {
      if (key === "sort") {
        return;
      }
      if (typeof value === "boolean") {
        if (value) {
          next.set(key, "true");
        }
      } else if (value.trim()) {
        next.set(key, value.trim());
      }
    });
    return next;
  }, [filters, page]);

  const refresh = useCallback(async () => {
    const result = await api.jobs(params);
    setData(result);
    setNotice(result.warning ? { type: "warning", message: result.warning } : null);
    setSelectedIds(new Set());
  }, [params]);

  useEffect(() => {
    setLoading(true);
    refresh()
      .then(() => setError(null))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (!rescoreRunId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.rescoreRun(rescoreRunId);
        if (cancelled) {
          return;
        }
        setRescoreRun(result);
        setRescorePollFailures(0);
        if (isTerminalRescoreStatus(result.status)) {
          setRescoreRunId(null);
          setRescoring(false);
          await refresh();
          setNotice({
            type: result.status === "completed" ? "success" : "error",
            message:
              result.status === "completed"
                ? `Rescoring complete: ${result.completed_jobs} completed, ${result.skipped} skipped, ${result.failed_jobs} failed.`
                : result.error ?? "Rescoring failed"
          });
        }
      } catch (err) {
        if (!cancelled) {
          setRescorePollFailures((current) => {
            const next = current + 1;
            if (next >= 3) {
              setNotice({ type: "warning", message: err instanceof Error ? err.message : "Temporary rescore status polling failure" });
            }
            return next;
          });
        }
      }
    };
    poll();
    const intervalId = globalThis.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      globalThis.clearInterval(intervalId);
    };
  }, [refresh, rescoreRunId]);

  useEffect(() => {
    if (!availabilityRunId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.availabilityRun(availabilityRunId);
        if (cancelled) {
          return;
        }
        setAvailabilityRun(result);
        setAvailabilityPollFailures(0);
        if (result.status === "completed" || result.status === "failed") {
          setAvailabilityRunId(null);
          setCheckingAvailability(false);
          await refresh();
          setNotice({
            type: result.status === "completed" ? "success" : "error",
            message:
              result.status === "completed"
                ? `${result.checked} jobs checked for availability, ${result.failed} failed.`
                : result.error ?? "Availability check failed"
          });
        }
      } catch (err) {
        if (!cancelled) {
          setAvailabilityPollFailures((current) => {
            const next = current + 1;
            if (next >= 3) {
              setNotice({ type: "warning", message: err instanceof Error ? err.message : "Temporary availability polling failure" });
            }
            return next;
          });
        }
      }
    };
    poll();
    const intervalId = globalThis.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      globalThis.clearInterval(intervalId);
    };
  }, [availabilityRunId, refresh]);

  const runAction = async (action: () => Promise<unknown>) => {
    setActionLoading(true);
    try {
      await action();
      await refresh();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Job cleanup action failed");
    } finally {
      setActionLoading(false);
    }
  };

  const selectedJobIds = Array.from(selectedIds);
  const selectedCount = selectedJobIds.length;

  const deleteJobs = (jobIds: number[]) => {
    const message =
      jobIds.length === 1
        ? "Permanently delete this job and its related analysis, scores, skills, missing skills, and saved/applied records?"
        : `Permanently delete ${jobIds.length} jobs and their related analysis, scores, skills, missing skills, and saved/applied records?`;
    if (!window.confirm(message)) {
      return;
    }
    void runAction(() => (jobIds.length === 1 ? api.deleteJob(jobIds[0]) : api.bulkDeleteJobs(jobIds)));
  };

  const excludeJobs = (jobIds: number[]) => {
    void runAction(() => api.bulkExcludeJobs(jobIds));
  };

  const rescoreJobs = async () => {
    if (rescoring) {
      return;
    }
    setRescoring(true);
    setNotice({ type: "info", message: "Rescoring jobs..." });
    setRescoreRun(null);
    setRescorePollFailures(0);
    setError(null);
    try {
      const started = await api.rescoreJobs();
      setRescoreRunId(started.run_id);
      setRescoreRun(emptyRescoreRun(started.run_id, started.status));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Rescoring jobs failed";
      if (err instanceof ApiError && err.status === 400 && message.toLowerCase().includes("profile")) {
        setNotice({ type: "warning", message: "Upload and save your profile before scoring jobs." });
      } else {
        setNotice({ type: "error", message });
      }
      setRescoring(false);
    }
  };

  const retryRescore = async () => {
    if (!rescoreRun || rescoring) {
      return;
    }
    setRescoring(true);
    setNotice({ type: "info", message: "Retrying rescore run..." });
    setError(null);
    try {
      const started = await api.retryRescoreRun(rescoreRun.run_id);
      setRescoreRunId(started.run_id);
      setRescoreRun(emptyRescoreRun(started.run_id, started.status));
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Unable to retry rescore run" });
      setRescoring(false);
    }
  };

  const cancelRescore = async () => {
    if (!rescoreRun || !rescoring) {
      return;
    }
    try {
      const canceled = await api.cancelRescoreRun(rescoreRun.run_id);
      setRescoreRun({ ...rescoreRun, status: canceled.status });
      setRescoreRunId(null);
      setRescoring(false);
      setNotice({ type: "warning", message: "Rescoring canceled." });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Unable to cancel rescore run" });
    }
  };

  const checkAvailability = async () => {
    if (checkingAvailability) {
      return;
    }
    setCheckingAvailability(true);
    setNotice({ type: "info", message: "Checking job availability..." });
    setAvailabilityRun(null);
    setAvailabilityPollFailures(0);
    setError(null);
    try {
      const started = await api.checkJobsAvailability(selectedCount > 0 ? selectedJobIds : undefined);
      setAvailabilityRunId(started.run_id);
      setAvailabilityRun(emptyAvailabilityRun(started.run_id, started.status));
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Availability check failed" });
      setCheckingAvailability(false);
    }
  };

  const openScorecard = async (jobId: number) => {
    setScorecardLoading(true);
    setScorecard(null);
    setNotice(null);
    try {
      setScorecard(await api.jobScorecard(jobId));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to load scorecard";
      if (err instanceof ApiError && err.status === 400 && message.toLowerCase().includes("profile")) {
        setNotice({ type: "warning", message: "Upload and save your profile before scoring jobs." });
      } else {
        setNotice({ type: "error", message });
      }
    } finally {
      setScorecardLoading(false);
    }
  };

  return (
    <div className="page-stack">
      <FiltersBar
        filters={filters}
        onChange={(next) => {
          setFilters(next);
          setPage(1);
        }}
        onReset={() => {
          setFilters(initialFilters);
          setPage(1);
        }}
      />
      <div className="action-row">
        <button type="button" className="primary-button" disabled={rescoring} onClick={() => void rescoreJobs()}>
          {rescoring ? <span className="spinner" aria-hidden="true" /> : null}
          {rescoring ? "Rescoring..." : "Rescore jobs"}
        </button>
        <button type="button" className="secondary-button" disabled={checkingAvailability} onClick={() => void checkAvailability()}>
          {checkingAvailability ? <span className="spinner" aria-hidden="true" /> : null}
          {checkingAvailability ? "Checking..." : selectedCount > 0 ? "Check selected availability" : "Check availability"}
        </button>
      </div>
      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}
      {rescoring ? (
        <div className="notice-banner info">
          Rescoring jobs...
          {rescoreRun ? ` ${rescoreRun.completed_jobs} / ${rescoreRun.total_jobs} completed, ${rescoreRun.failed_jobs} failed${formatEta(rescoreRun.estimated_seconds_remaining)}.` : ""}
          {rescoreRun ? (
            <button type="button" className="secondary-button compact-button" onClick={() => void cancelRescore()}>
              Cancel
            </button>
          ) : null}
        </div>
      ) : null}
      {rescoreRun && rescoreRun.status === "stalled" ? (
        <div className="notice-banner error">
          Rescoring stalled. {rescoreRun.completed_jobs} / {rescoreRun.total_jobs} completed, {rescoreRun.failed_jobs} failed.
          <button type="button" className="secondary-button compact-button" onClick={() => void retryRescore()}>
            Retry failed run
          </button>
        </div>
      ) : null}
      {availabilityRunId && availabilityRun ? (
        <div className="notice-banner info">
          Checking availability... {availabilityRun.processed} / {availabilityRun.total} processed, {availabilityRun.checked} checked, {availabilityRun.failed} failed.
        </div>
      ) : null}
      {error ? <ErrorState message={error} /> : null}
      {loading ? <LoadingState label="Loading jobs" /> : null}
      {!loading && !error && data?.items.length === 0 ? (
        <EmptyState title="No jobs found" message="Adjust the filters or clear the search criteria." />
      ) : null}
      {!loading && !error && data && data.items.length > 0 ? (
        <section className="panel">
          <div className="panel-header">
            <h2>{data.total_count} jobs</h2>
            <div className="panel-actions">
              <span className="muted-text">{selectedCount} selected</span>
              <button type="button" className="secondary-button" disabled={selectedCount === 0 || actionLoading} onClick={() => excludeJobs(selectedJobIds)}>
                Exclude selected
              </button>
              <button type="button" className="danger-button" disabled={selectedCount === 0 || actionLoading} onClick={() => deleteJobs(selectedJobIds)}>
                Delete selected
              </button>
              <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={setPage} />
            </div>
          </div>
          <JobsTable
            jobs={data.items}
            selectedIds={selectedIds}
            onToggle={(jobId, checked) => {
              setSelectedIds((current) => {
                const next = new Set(current);
                if (checked) {
                  next.add(jobId);
                } else {
                  next.delete(jobId);
                }
                return next;
              });
            }}
            onToggleAll={(checked) => {
              setSelectedIds((current) => {
                const next = new Set(current);
                data.items.forEach((job) => {
                  if (checked) {
                    next.add(job.id);
                  } else {
                    next.delete(job.id);
                  }
                });
                return next;
              });
            }}
            onDelete={(jobId) => deleteJobs([jobId])}
            onExclude={(jobId) => excludeJobs([jobId])}
            onScorecard={(jobId) => void openScorecard(jobId)}
            onCheckAvailability={async (jobId) => {
              await api.checkJobAvailability(jobId);
              await refresh();
            }}
          />
          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={setPage} />
        </section>
      ) : null}
      {scorecardLoading ? (
        <div className="modal-backdrop">
          <div className="modal-panel scorecard-modal">
            <LoadingState label="Loading scorecard" />
          </div>
        </div>
      ) : null}
      {scorecard ? <ScorecardModal scorecard={scorecard} onClose={() => setScorecard(null)} /> : null}
    </div>
  );
}

function ScorecardModal({ scorecard, onClose }: { scorecard: JobScorecard; onClose: () => void }) {
  return (
    <div className="modal-backdrop">
      <div className="modal-panel scorecard-modal">
        <div className="modal-header">
          <div>
            <h2>Scorecard</h2>
            <p className="muted-text">{scorecard.why}</p>
          </div>
          <button type="button" className="secondary-button compact-button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="scorecard-summary">
          <div>
            <span className="muted-text">Score</span>
            <strong>{Math.round(Number(scorecard.total_score))}</strong>
          </div>
          <div>
            <span className="muted-text">Tier</span>
            <strong>{scorecard.tier}</strong>
          </div>
          <div>
            <span className="muted-text">Recommendation</span>
            <strong>{scorecard.recommendation}</strong>
          </div>
          <div>
            <span className="muted-text">Confidence</span>
            <strong>{Math.round(Number(scorecard.confidence_score))}</strong>
          </div>
        </div>
        <div className="scorecard-grid">
          <ScorecardList title="Matched skills" items={scorecard.matched_skills} />
          <ScorecardList title="Missing skills" items={scorecard.missing_skills} />
          <ScorecardList title="Risks" items={[...scorecard.gates, ...scorecard.risks]} />
          <ScorecardList title="Evidence" items={scorecard.matched_evidence} />
        </div>
        <section className="scorecard-section">
          <h3>Score breakdown</h3>
          <div className="breakdown-list">
            {Object.entries(scorecard.score_breakdown).map(([label, value]) => (
              <div key={label} className="breakdown-row">
                <span>{label.replaceAll("_", " ")}</span>
                <strong>{Number(value).toFixed(2)}</strong>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function ScorecardList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="scorecard-section">
      <h3>{title}</h3>
      {items.length > 0 ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="muted-text">None</p>
      )}
    </section>
  );
}

function isTerminalRescoreStatus(status: string): boolean {
  return status === "completed" || status === "failed" || status === "stalled" || status === "canceled";
}

function emptyRescoreRun(runId: number, status: string): JobRescoreRunStatus {
  return {
    run_id: runId,
    status,
    total: 0,
    scored: 0,
    skipped: 0,
    failed: 0,
    total_jobs: 0,
    completed_jobs: 0,
    failed_jobs: 0,
    estimated_seconds_remaining: null,
    started_at: null,
    finished_at: null,
    last_heartbeat_at: null,
    error: null
  };
}

function emptyAvailabilityRun(runId: number, status: string): JobAvailabilityRunStatus {
  return {
    run_id: runId,
    status,
    total: 0,
    processed: 0,
    checked: 0,
    failed: 0,
    error: null,
    started_at: null,
    finished_at: null,
    last_heartbeat_at: null
  };
}

function formatEta(seconds: number | null): string {
  if (seconds === null) {
    return "";
  }
  if (seconds < 60) {
    return `, about ${Math.max(1, Math.round(seconds))}s remaining`;
  }
  return `, about ${Math.round(seconds / 60)}m remaining`;
}
