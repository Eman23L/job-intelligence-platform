"use client";

import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { StatCard } from "@/components/StatCard";
import { api } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import type { AnalyticsOverview } from "@/types/api";

export default function DashboardPage() {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [slowLoading, setSlowLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const slowTimer = window.setTimeout(() => {
      if (active) {
        setSlowLoading(true);
      }
    }, 2500);
    setLoading(true);
    setSlowLoading(false);
    setError(null);
    api
      .overview()
      .then((result) => {
        if (!active) {
          return;
        }
        setData(result);
      })
      .catch((err: Error) => {
        if (!active) {
          return;
        }
        setError(err.message);
      })
      .finally(() => {
        if (active) {
          setLoading(false);
          setSlowLoading(false);
        }
      });

    return () => {
      active = false;
      window.clearTimeout(slowTimer);
    };
  }, []);

  if (loading) {
    return <LoadingState label={slowLoading ? "Warming up analytics" : "Loading overview"} />;
  }
  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <ErrorState message="The overview response was empty." />;
  }

  return (
    <div className="page-stack">
      <div className="stats-grid">
        <StatCard label="Total jobs" value={data.total_jobs} />
        <StatCard label="Analysed jobs" value={data.analysed_jobs} />
        <StatCard label="Scored jobs" value={data.scored_jobs} />
        <StatCard label="Saved jobs" value={data.saved_jobs} />
        <StatCard label="Applied jobs" value={data.applied_jobs} />
        <StatCard label="Excellent matches" value={data.excellent_matches} />
        <StatCard label="Strong matches" value={data.strong_matches} />
        <StatCard label="Stretch roles" value={data.stretch_roles} />
        <StatCard label="Excluded jobs" value={data.excluded_jobs} />
        <StatCard label="Average score" value={formatNumber(data.average_score)} />
        <StatCard label="Newest job" value={formatDate(data.newest_job_date)} />
      </div>
    </div>
  );
}
