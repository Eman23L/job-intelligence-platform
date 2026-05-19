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
    try {
      await api.updateSavedJob(id, { status });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update saved job");
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
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Company</th>
              <th>Status</th>
              <th>Tier</th>
              <th>Saved</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link href={`/jobs/${item.job_id}`} className="table-link">
                    {item.job?.title ?? `Job ${item.job_id}`}
                  </Link>
                </td>
                <td>{item.job?.company_name ?? "Unknown"}</td>
                <td>
                  <select value={item.status} onChange={(event) => updateStatus(item.id, event.target.value)}>
                    {statuses.map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <RecommendationBadge tier={item.job?.recommendation_tier} />
                </td>
                <td>{formatDate(item.saved_at)}</td>
                <td>{item.notes ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
