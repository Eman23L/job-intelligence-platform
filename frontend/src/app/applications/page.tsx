"use client";

import { useEffect, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import { RecommendationActionBadge } from "@/components/RecommendationActionBadge";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScoreBadge } from "@/components/ScoreBadge";
import { api } from "@/lib/api";
import type { ApplicationItem, ApplicationsList, JobScorecard } from "@/types/api";

const RECENT_CHECK_MS = 24 * 60 * 60 * 1000;

export default function ApplicationsPage() {
  const [data, setData] = useState<ApplicationsList | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<number | "prepare" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "info" | "success" | "warning" | "error"; message: string } | null>(null);
  const [scorecard, setScorecard] = useState<JobScorecard | null>(null);

  const refresh = async () => {
    const result = await api.applications();
    setData(result);
  };

  useEffect(() => {
    setLoading(true);
    refresh()
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const prepareApplications = async () => {
    setActionLoading("prepare");
    setError(null);
    try {
      const result = await api.prepareApplications();
      await refresh();
      setNotice({
        type: "success",
        message: result.queued === 1 ? "1 job added to the application queue." : `${result.queued} jobs added to the application queue.`
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to prepare applications");
    } finally {
      setActionLoading(null);
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
      if (!["active", "unknown"].includes(checked.availability_status)) {
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

  if (loading) {
    return <LoadingState label="Loading applications" />;
  }

  return (
    <div className="page-stack">
      <div className="action-row">
        <button type="button" className="primary-button" disabled={actionLoading === "prepare"} onClick={() => void prepareApplications()}>
          {actionLoading === "prepare" ? <span className="spinner" aria-hidden="true" /> : null}
          {actionLoading === "prepare" ? "Preparing..." : "Prepare applications"}
        </button>
      </div>
      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}
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
                    </td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void openApplyLink(item)}>
                          Open apply link
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
