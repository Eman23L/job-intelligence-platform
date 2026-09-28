"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScorePill } from "@/components/ScoreBadge";
import { StatCard } from "@/components/StatCard";
import { api } from "@/lib/api";
import { formatDate, formatNumber, formatSalary } from "@/lib/format";
import type { AnalyticsOverview, JobListItem } from "@/types/api";

const TOP_MATCH_PARAMS = new URLSearchParams({ page: "1", page_size: "5", sort: "total_score_desc", exclude_excluded: "true" });

export default function DashboardPage() {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [topJobs, setTopJobs] = useState<JobListItem[]>([]);
  const [hasProfile, setHasProfile] = useState(true);
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
    // Secondary widgets load independently so a slow jobs query never blocks the overview.
    api
      .jobs(TOP_MATCH_PARAMS)
      .then((result) => active && setTopJobs(result.items.filter((job) => job.total_score !== null)))
      .catch(() => undefined);
    api
      .profile()
      .then((profile) => active && setHasProfile(Boolean(profile?.cv_text)))
      .catch(() => undefined);
    api
      .overview()
      .then((result) => active && setData(result))
      .catch((err: Error) => active && setError(err.message))
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
    return <LoadingState label={slowLoading ? "Waking up the server" : "Loading overview"} />;
  }
  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <ErrorState message="The overview response was empty." />;
  }

  const goodMatches = data.excellent_matches + data.strong_matches;
  const otherScored = Math.max(0, data.scored_jobs - goodMatches - data.stretch_roles - data.excluded_jobs);
  const breakdown = [
    { label: "Excellent match", value: data.excellent_matches, color: "#16a34a" },
    { label: "Strong match", value: data.strong_matches, color: "#4f46e5" },
    { label: "Stretch role", value: data.stretch_roles, color: "#f59e0b" },
    { label: "Other", value: otherScored, color: "#cbd5e1" },
    { label: "Excluded", value: data.excluded_jobs, color: "#ef4444" }
  ].filter((item) => item.value > 0);
  const breakdownTotal = breakdown.reduce((sum, item) => sum + item.value, 0);

  return (
    <div className="page-stack">
      {!hasProfile ? (
        <section className="panel welcome-card">
          <h2>Start by adding your CV</h2>
          <p>We use it to score every job against your skills and target roles, so the best matches rise to the top.</p>
          <div className="action-row">
            <Link href="/profile" className="button-link">
              Add your CV
            </Link>
            <Link href="/sources" className="button-link secondary">
              Set up job sources
            </Link>
          </div>
        </section>
      ) : null}

      <div className="stats-grid">
        <StatCard label="Jobs found" value={data.total_jobs} hint={data.newest_job_date ? `Newest ${formatDate(data.newest_job_date)}` : undefined} />
        <StatCard label="Good matches" value={goodMatches} hint="Excellent or strong fit" />
        <StatCard label="Saved" value={data.saved_jobs} />
        <StatCard label="Applied" value={data.applied_jobs} />
      </div>

      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Top matches</h2>
              <p className="muted-text">Your highest-scoring roles right now.</p>
            </div>
            <Link href="/jobs" className="button-link secondary compact-button">
              View all jobs
            </Link>
          </div>
          {topJobs.length === 0 ? (
            <p className="muted">No scored jobs yet. Add your CV on the Profile page, then rescore jobs.</p>
          ) : (
            <div>
              {topJobs.map((job) => (
                <div key={job.id} className="top-match">
                  <ScorePill score={job.total_score} />
                  <div className="job-cell">
                    <Link href={`/jobs/${job.id}`} className="table-link">
                      {job.title}
                    </Link>
                    <span className="job-meta">
                      {[job.company_name, job.location, formatSalary(job.normalized_annual_min, job.normalized_annual_max, job.salary_currency)]
                        .filter((part) => part && part !== "Not listed")
                        .join(" · ")}
                    </span>
                  </div>
                  <span className="top-match-tier">
                    <RecommendationBadge tier={job.recommendation_tier} />
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>How jobs match</h2>
              <p className="muted-text">Average score {formatNumber(data.average_score)}</p>
            </div>
          </div>
          {breakdownTotal > 0 ? (
            <>
              <div className="match-bar" role="img" aria-label={breakdown.map((item) => `${item.label}: ${item.value}`).join(", ")}>
                {breakdown.map((item) => (
                  <span key={item.label} style={{ width: `${(item.value / breakdownTotal) * 100}%`, background: item.color }} />
                ))}
              </div>
              <div className="match-legend">
                {breakdown.map((item) => (
                  <div key={item.label}>
                    <span>
                      <span className="legend-dot" style={{ background: item.color }} />
                      {item.label}
                    </span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">Jobs will appear here once they have been scored.</p>
          )}
        </section>
      </div>
    </div>
  );
}
