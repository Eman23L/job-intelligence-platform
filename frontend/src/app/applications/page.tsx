"use client";

import { useCallback, useEffect, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import { RecommendationActionBadge } from "@/components/RecommendationActionBadge";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScoreBadge } from "@/components/ScoreBadge";
import { ApiError, api } from "@/lib/api";
import type { ApplicationItem, ApplicationPrepareRunStatus, ApplicationsList, AssistApplyResult, JobScorecard } from "@/types/api";

const RECENT_CHECK_MS = 24 * 60 * 60 * 1000;

export default function ApplicationsPage() {
  const [data, setData] = useState<ApplicationsList | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<number | "prepare" | null>(null);
  const [prepareRunId, setPrepareRunId] = useState<number | null>(null);
  const [prepareRun, setPrepareRun] = useState<ApplicationPrepareRunStatus | null>(null);
  const [preparePollFailures, setPreparePollFailures] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "info" | "success" | "warning" | "error"; message: string } | null>(null);
  const [scorecard, setScorecard] = useState<JobScorecard | null>(null);
  const [assistResult, setAssistResult] = useState<{ jobTitle: string; result: AssistApplyResult } | null>(null);
  const [threshold, setThreshold] = useState(80);

  const refresh = useCallback(async () => {
    const result = await api.applications();
    setData(result);
    setThreshold(result.minimum_apply_score);
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh()
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (!prepareRunId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.prepareApplicationsRun(prepareRunId);
        if (cancelled) {
          return;
        }
        setPrepareRun(result);
        setPreparePollFailures(0);
        if (result.status === "completed" || result.status === "failed") {
          setPrepareRunId(null);
          setActionLoading(null);
          await refresh();
          setNotice({
            type: result.status === "completed" ? "success" : "error",
            message:
              result.status === "completed"
                ? `Prepare complete: ${result.queued} queued, ${result.skipped} skipped, ${result.failed} failed.`
                : result.error ?? "Prepare applications failed"
          });
        }
      } catch (err) {
        if (!cancelled) {
          setPreparePollFailures((current) => {
            const next = current + 1;
            if (next >= 3) {
              setNotice({ type: "warning", message: err instanceof Error ? err.message : "Temporary prepare status polling failure" });
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
  }, [prepareRunId, refresh]);

  const prepareApplications = async () => {
    setActionLoading("prepare");
    setError(null);
    setNotice({ type: "info", message: "Preparing applications..." });
    setPrepareRun(null);
    setPreparePollFailures(0);
    try {
      const started = await api.prepareApplications();
      setPrepareRunId(started.run_id);
      setPrepareRun(emptyPrepareRun(started.run_id, started.status));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to prepare applications");
      setActionLoading(null);
    }
  };

  const saveThreshold = async (value: number) => {
    setThreshold(value);
    setError(null);
    try {
      await api.saveApplicationProfile({ minimum_apply_score: value });
      await refresh();
      setNotice({ type: "success", message: `Apply threshold set to ${value}+. Queue refreshed.` });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save apply threshold");
    }
  };

  const runJobAction = async (jobId: number, action: () => Promise<unknown>, successMessage: string) => {
    setActionLoading(jobId);
    setError(null);
    try {
      await action();
      await refresh();
      setNotice({ type: "success", message: successMessage });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Application action failed");
    } finally {
      setActionLoading(null);
    }
  };

  const openApplyLink = async (item: ApplicationItem) => {
    setActionLoading(item.job_id);
    setError(null);
    try {
      const checked = await api.checkJobAvailability(item.job_id);
      await refresh();
      if (checked.availability_status !== "active") {
        setNotice({
          type: "warning",
          message: `Application blocked because the job is ${checked.availability_status}. ${checked.availability_reason ?? ""}`.trim()
        });
        return;
      }
      window.open(item.apply_url, "_blank", "noopener,noreferrer");
      await api.markOpened(item.job_id);
      await refresh();
      setNotice({ type: "success", message: "Application link opened." });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to open apply link");
    } finally {
      setActionLoading(null);
    }
  };

  const viewScorecard = async (jobId: number) => {
    setActionLoading(jobId);
    setError(null);
    try {
      setScorecard(await api.jobScorecard(jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load scorecard");
    } finally {
      setActionLoading(null);
    }
  };

  const assistApply = async (item: ApplicationItem, mode: "review_only" | "submit_with_confirmation") => {
    if (mode === "submit_with_confirmation") {
      const confirmed = window.confirm("This will submit the application on JobServe using your saved CV. Continue?");
      if (!confirmed) {
        return;
      }
    }
    setActionLoading(item.job_id);
    setError(null);
    setAssistResult(null);
    try {
      const result = await api.assistApply(item.job_id, mode);
      await refresh();
      setAssistResult({ jobTitle: item.title, result });
      setNotice({
        type: result.submitted ? "success" : "warning",
        message: result.submitted ? "JobServe application submitted." : "Assisted apply started. Review the browser manually before taking any next action."
      });
    } catch (err) {
      if (err instanceof ApiError && err.code === "worker_unavailable") {
        setNotice({ type: "error", message: "Browser automation worker is offline" });
      } else {
        setError(err instanceof Error ? err.message : "Unable to assist apply");
      }
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return <LoadingState label="Loading applications" />;
  }

  return (
    <div className="page-stack">
      <div className="action-row">
        <label>
          Only prepare/apply for jobs scoring at least
          <select value={threshold} onChange={(event) => void saveThreshold(Number(event.target.value))}>
            <option value={60}>60+</option>
            <option value={70}>70+</option>
            <option value={80}>80+</option>
            <option value={85}>85+</option>
            <option value={90}>90+</option>
          </select>
        </label>
        <button type="button" className="primary-button" disabled={actionLoading === "prepare"} onClick={() => void prepareApplications()}>
          {actionLoading === "prepare" ? <span className="spinner" aria-hidden="true" /> : null}
          {actionLoading === "prepare" ? "Preparing..." : "Prepare applications"}
        </button>
      </div>
      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}
      <div className="notice-banner info">Current apply threshold: {threshold}+.</div>
      {prepareRunId && prepareRun ? (
        <div className="notice-banner info">
          Preparing applications... {prepareRun.processed} / {prepareRun.total} processed, {prepareRun.queued} queued, {prepareRun.failed} failed.
        </div>
      ) : null}
      {error ? <ErrorState message={error} /> : null}
      {!error && data?.items.length === 0 ? (
        <EmptyState title="No applications queued" message="Prepare applications to queue high-scoring jobs for manual submission." />
      ) : null}
      {!error && data && data.items.length > 0 ? (
        <section className="panel">
          <div className="panel-header">
            <h2>{data.items.length} ready applications</h2>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Location</th>
                  <th>Score</th>
                  <th>Tier</th>
                  <th>Recommendation</th>
                  <th>Status</th>
                  <th>Availability</th>
                  <th>Apply route</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.job_id}>
                    <td>
                      <strong>{item.title}</strong>
                      <div className="muted-text">{item.company_name ?? "Unknown company"}</div>
                    </td>
                    <td>{item.location ?? "Not listed"}</td>
                    <td>
                      <ScoreBadge score={item.total_score} />
                    </td>
                    <td>
                      <RecommendationBadge tier={item.recommendation_tier} />
                    </td>
                    <td>
                      <RecommendationActionBadge recommendation={item.recommendation} />
                    </td>
                    <td>
                      {item.application_status.replaceAll("_", " ")}
                      {isAvailabilityCheckStale(item.last_checked_at) ? <div className="status-note">Availability check needed</div> : null}
                    </td>
                    <td>
                      <AvailabilityBadge status={item.availability_status} />
                      <div className="muted-text">{item.last_checked_at ? `Checked ${formatShortDate(item.last_checked_at)}` : "Not checked"}</div>
                      {item.availability_reason ? <div className="muted-text">{item.availability_reason}</div> : null}
                    </td>
                    <td>
                      <span className={`badge apply-difficulty-${item.apply_difficulty} apply-strategy-${item.apply_strategy}`}>
                        {formatLabel(item.apply_strategy)}
                      </span>
                      <div className="status-note">{formatLabel(item.apply_difficulty)}</div>
                      {item.apply_strategy_reason ? <div className="muted-text">{item.apply_strategy_reason}</div> : null}
                      {item.apply_readiness_score ? <div className="muted-text">Readiness {Math.round(Number(item.apply_readiness_score))}</div> : null}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void openApplyLink(item)}>
                          Open apply page
                        </button>
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void assistApply(item, "review_only")}>
                          Assist fill
                        </button>
                        <button
                          type="button"
                          className="secondary-button compact-button"
                          disabled={actionLoading === item.job_id || item.apply_strategy !== "jobserve_apply_easy"}
                          onClick={() => void assistApply(item, "submit_with_confirmation")}
                        >
                          Submit JobServe application
                        </button>
                        <button
                          type="button"
                          className="secondary-button compact-button"
                          disabled={actionLoading === item.job_id}
                          onClick={() => void runJobAction(item.job_id, () => api.markApplied(item.job_id), "Application marked applied.")}
                        >
                          Mark applied
                        </button>
                        <button
                          type="button"
                          className="secondary-button compact-button"
                          disabled={actionLoading === item.job_id}
                          onClick={() => void runJobAction(item.job_id, () => api.markSkipped(item.job_id), "Application skipped.")}
                        >
                          Skip
                        </button>
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void viewScorecard(item.job_id)}>
                          View scorecard
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {scorecard ? <ScorecardModal scorecard={scorecard} onClose={() => setScorecard(null)} /> : null}
      {assistResult ? <AssistApplyModal data={assistResult} onClose={() => setAssistResult(null)} /> : null}
    </div>
  );
}

function AssistApplyModal({ data, onClose }: { data: { jobTitle: string; result: AssistApplyResult }; onClose: () => void }) {
  return (
    <div className="modal-backdrop">
      <div className="modal-panel scorecard-modal">
        <div className="modal-header">
          <div>
            <h2>Assist apply</h2>
            <p className="muted-text">{data.jobTitle}</p>
          </div>
          <button type="button" className="secondary-button compact-button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="notice-banner warning">Review manually in the browser. The assistant did not submit the application.</div>
        <section className="scorecard-section">
          <h3>Filled fields</h3>
          <FieldList items={data.result.filled_fields} empty="No fields filled" />
        </section>
        <section className="scorecard-section">
          <h3>Unfilled fields</h3>
          <FieldList items={data.result.unfilled_fields} empty="No unfilled fields detected" />
        </section>
        <section className="scorecard-section">
          <h3>Required fields not filled</h3>
          <FieldList items={data.result.unfilled_required_fields} empty="No missing required fields reported" />
        </section>
        <section className="scorecard-section">
          <h3>Result</h3>
          <div className="metric-list">
            <Metric label="CV uploaded" value={data.result.uploaded_cv ? "Yes" : "No"} />
            <Metric label="Submitted" value={data.result.submitted ? "Yes" : "No"} />
          </div>
        </section>
        <section className="scorecard-section">
          <h3>Warnings</h3>
          <FieldList items={data.result.warnings} empty="No warnings" />
        </section>
      </div>
    </div>
  );
}

function FieldList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="muted-text">{empty}</p>;
  }
  return (
    <ul>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{value}</strong>
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
        </div>
      </div>
    </div>
  );
}

function isAvailabilityCheckStale(value: string | null): boolean {
  if (!value) {
    return true;
  }
  return Date.now() - new Date(value).getTime() > RECENT_CHECK_MS;
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(
    new Date(value)
  );
}

function formatLabel(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "unknown";
}

function emptyPrepareRun(runId: number, status: string): ApplicationPrepareRunStatus {
  return {
    run_id: runId,
    status,
    total: 0,
    processed: 0,
    queued: 0,
    skipped: 0,
    failed: 0,
    error: null,
    started_at: null,
    finished_at: null,
    last_heartbeat_at: null
  };
}
