"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import type { UnifiedRun, UnifiedRunList } from "@/types/api";

const typeOptions = [
  ["all", "All"],
  ["scrape", "Scrape"],
  ["rescore", "Rescore"],
  ["availability", "Availability"],
  ["application_prepare", "Application prepare"],
  ["apply_strategy", "Apply strategy"]
];

const statusOptions = [
  ["all", "All"],
  ["queued", "Queued"],
  ["running", "Running"],
  ["completed", "Completed"],
  ["failed", "Failed"],
  ["stalled", "Stalled"]
];

export default function RunsPage() {
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [data, setData] = useState<UnifiedRunList | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "warning" | "error"; message: string } | null>(null);

  const params = useMemo(() => {
    const next = new URLSearchParams();
    next.set("type", typeFilter);
    next.set("status", statusFilter);
    next.set("limit", "50");
    return next;
  }, [statusFilter, typeFilter]);

  const refresh = useCallback(async () => {
    const result = await api.runs(params);
    setData(result);
    setError(null);
  }, [params]);

  useEffect(() => {
    setLoading(true);
    refresh()
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (!data?.items.some((run) => run.status === "queued" || run.status === "running" || run.status === "pending")) {
      return;
    }
    const intervalId = globalThis.setInterval(() => {
      void refresh().catch((err: Error) => setNotice({ type: "warning", message: err.message }));
    }, 5000);
    return () => globalThis.clearInterval(intervalId);
  }, [data, refresh]);

  const runAction = async (run: UnifiedRun, action: "retry" | "cancel") => {
    const key = `${action}-${run.type}-${run.id}`;
    setActionLoading(key);
    setNotice(null);
    try {
      if (action === "retry") {
        await api.retryRun(run.type, run.id);
      } else {
        await api.cancelRun(run.type, run.id);
      }
      await refresh();
      setNotice({ type: "success", message: action === "retry" ? "Run retry started." : "Run canceled." });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : `Unable to ${action} run` });
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return <LoadingState label="Loading system runs" />;
  }

  return (
    <div className="page-stack">
      <section className="filters-bar">
        <label>
          Type
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            {typeOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            {statusOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </section>

      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}
      {error ? <ErrorState message={error} /> : null}
      {!error && data?.items.length === 0 ? <EmptyState title="No runs found" message="Adjust filters or start a background workflow." /> : null}
      {!error && data && data.items.length > 0 ? (
        <section className="panel">
          <div className="panel-header">
            <h2>System Runs</h2>
            <span className="muted-text">{data.items.length} shown</span>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Result</th>
                  <th>Duration</th>
                  <th>Error</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((run) => (
                  <tr key={`${run.type}-${run.id}`}>
                    <td>
                      <strong>{formatType(run.type)}</strong>
                      <div className="muted-text">#{run.id}</div>
                    </td>
                    <td>
                      <span className={`badge run-status-${run.status}`}>{run.status}</span>
                    </td>
                    <td>
                      <div className="progress-cell">
                        <div className="progress-track">
                          <div className="progress-fill" style={{ width: `${progressPercent(run)}%` }} />
                        </div>
                        <span className="muted-text">
                          {run.processed} / {run.total}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="compact-counts">
                        <span>{run.succeeded} ok</span>
                        <span>{run.failed} failed</span>
                        <span>{run.skipped} skipped</span>
                      </div>
                    </td>
                    <td>{formatDuration(run.duration_seconds)}</td>
                    <td>
                      <span className="muted-text">{run.error ? truncate(run.error) : ""}</span>
                    </td>
                    <td>
                      <div className="row-actions">
                        {(run.status === "failed" || run.status === "stalled") && run.type !== "scrape" ? (
                          <button
                            type="button"
                            className="secondary-button compact-button"
                            disabled={actionLoading !== null}
                            onClick={() => void runAction(run, "retry")}
                          >
                            Retry
                          </button>
                        ) : null}
                        {run.status === "queued" || run.status === "running" || run.status === "pending" ? (
                          <button
                            type="button"
                            className="danger-button compact-button"
                            disabled={actionLoading !== null}
                            onClick={() => void runAction(run, "cancel")}
                          >
                            Cancel
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function progressPercent(run: UnifiedRun): number {
  if (run.total <= 0) {
    return run.status === "completed" ? 100 : 0;
  }
  return Math.max(0, Math.min(100, Math.round((run.processed / run.total) * 100)));
}

function formatType(value: string): string {
  return value.replaceAll("_", " ");
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) {
    return "";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function truncate(value: string): string {
  return value.length > 96 ? `${value.slice(0, 93)}...` : value;
}
