"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { SavedJob } from "@/types/api";

const statuses = ["saved", "rejected", "applied", "interviewing", "offer", "closed"];

export default function SavedPage() {
  const [items, setItems] = useState<SavedJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    api
      .savedJobs()
      .then((result) => {
        setItems(result);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const updateStatus = async (id: number, status: string) => {
    setUpdatingId(id);
    setUpdateError(null);
    try {
      const updated = await api.updateSavedJob(id, { status });
      setItems((current) => current.map((item) => (item.id === id ? { ...item, ...updated, job: updated.job ?? item.job } : item)));
    } catch (err) {
      setUpdateError(err instanceof Error ? err.message : "Unable to update saved job");
    } finally {
      setUpdatingId(null);
    }
  };

  if (loading) {
    return <LoadingState label="Loading saved jobs" />;
  }
  if (error) {
    return <ErrorState message={error} />;
  }
  if (!items.length) {
    return <EmptyState title="No saved jobs" message="Saved and rejected roles will appear here." />;
  }

  return (
    <section className="panel">
      {updateError ? <div className="notice-banner error">{updateError}</div> : null}
      <div className="table-wrap">
        <table className="data-table stack-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Company</th>
              <th>Status</th>
              <th>Match</th>
              <th>Saved</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td data-label="Job">
                  <Link href={`/jobs/${item.job_id}`} className="table-link">
                    {item.job?.title ?? `Job ${item.job_id}`}
                  </Link>
                </td>
                <td data-label="Company">{item.job?.company_name ?? "Unknown"}</td>
                <td data-label="Status">
                  <select
                    value={item.status}
                    disabled={updatingId === item.id}
                    aria-label={`Status for ${item.job?.title ?? `job ${item.job_id}`}`}
                    onChange={(event) => updateStatus(item.id, event.target.value)}>
                    {statuses.map((status) => (
                      <option key={status} value={status}>
                        {status.charAt(0).toUpperCase() + status.slice(1)}
                      </option>
                    ))}
                  </select>
                </td>
                <td data-label="Match">
                  <RecommendationBadge tier={item.job?.recommendation_tier} />
                </td>
                <td data-label="Saved">{formatDate(item.saved_at)}</td>
                <td data-label="Notes">{item.notes ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
