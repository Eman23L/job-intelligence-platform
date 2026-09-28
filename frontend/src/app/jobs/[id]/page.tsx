"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScorePill } from "@/components/ScoreBadge";
import { SkillBadge } from "@/components/SkillBadge";
import { api } from "@/lib/api";
import { formatDate, formatNumber, formatSalary, formatSalaryPeriod } from "@/lib/format";
import type { JobDetail } from "@/types/api";

export default function JobDetailPage({ params }: { params: { id: string } }) {
  const [data, setData] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const load = () => {
    api
      .jobDetail(params.id)
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  };

  useEffect(load, [params.id]);

  const runAction = async (action: "save" | "reject" | "applied") => {
    setActionMessage(null);
    try {
      if (action === "save") {
        await api.saveJob(params.id);
      } else if (action === "reject") {
        await api.rejectJob(params.id);
      } else {
        await api.markApplied(params.id);
      }
      setActionMessage("Status updated");
      load();
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Action failed");
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <LoadingState label="Loading job detail" />;
  }

  const score = data.score;
  const breakdown = [
    { label: "Role match", value: score?.role_match_score },
    { label: "Skill match", value: score?.skill_match_score },
    { label: "Experience", value: score?.experience_score },
    { label: "Location", value: score?.location_score },
    { label: "Salary", value: score?.salary_score },
    { label: "Freshness", value: score?.freshness_score }
  ].filter((item) => item.value !== null && item.value !== undefined);
  const about = [
    { label: "Role type", value: data.analysis?.role_family },
    { label: "Seniority", value: data.analysis?.seniority_level },
    { label: "Work style", value: data.job.remote_type },
    { label: "Posted", value: data.job.posted_at ? formatDate(data.job.posted_at) : null }
  ].filter((item) => item.value);

  return (
    <div className="page-stack">
      <Link href="/jobs" className="back-link">
        ← All jobs
      </Link>
      <section className="panel">
        <div className="panel-header">
          <div className="job-title-block">
            {score ? <ScorePill score={score.total_score} /> : null}
            <div>
              <h2>{data.job.title}</h2>
              <p className="muted">
                {[data.job.company_name, data.job.location, formatSalary(data.job.normalized_annual_min, data.job.normalized_annual_max, data.job.salary_currency)]
                  .filter((part) => part && part !== "Not listed")
                  .join(" · ")}
                {formatSalaryPeriod(data.job.salary_period) && formatSalaryPeriod(data.job.salary_period) !== "year" && (data.job.salary_min_raw || data.job.salary_max_raw)
                  ? ` (${formatSalary(data.job.salary_min_raw, data.job.salary_max_raw, data.job.salary_currency)} per ${formatSalaryPeriod(data.job.salary_period)})`
                  : ""}
              </p>
              <div className="badge-list">
                {score ? <RecommendationBadge tier={score.recommendation_tier} /> : null}
                {data.saved_status ? <span className="badge strong">{capitalise(data.saved_status)}</span> : null}
              </div>
            </div>
          </div>
          <div className="action-row">
            {data.job.canonical_url ? (
              <a href={data.job.canonical_url} target="_blank" rel="noreferrer" className="button-link">
                View listing ↗
              </a>
            ) : null}
            <button type="button" className="secondary-button" onClick={() => runAction("save")}>
              Save
            </button>
            <button type="button" className="secondary-button" onClick={() => runAction("applied")}>
              Mark applied
            </button>
            <button type="button" className="ghost-button" onClick={() => runAction("reject")}>
              Not interested
            </button>
          </div>
        </div>
        {actionMessage ? <div className="notice-banner info">{actionMessage}</div> : null}
      </section>

      <div className="detail-grid">
        <section className="panel">
          <h3>Job description</h3>
          <p className="description">{data.job.description_text ?? "No description stored."}</p>
        </section>

        <div className="page-stack">
          <section className="panel">
            <h3>Your fit</h3>
            {score?.explanation ? <p className="muted fit-summary">{score.explanation}</p> : null}
            <h4 className="subheading">Skills you have</h4>
            <div className="badge-list">
              {data.matched_skills.length ? (
                data.matched_skills.map((skill) => <SkillBadge key={skill.id} label={skill.skill_name} tone="good" />)
              ) : (
                <span className="muted-text">None detected</span>
              )}
            </div>
            <h4 className="subheading">Skills you&apos;re missing</h4>
            <div className="badge-list">
              {data.missing_skills.length ? (
                data.missing_skills.map((skill) => (
                  <SkillBadge key={skill.id} label={skill.skill_name} tone={skill.learning_priority === "high" ? "warn" : "neutral"} />
                ))
              ) : (
                <span className="muted-text">None – you cover the key skills</span>
              )}
            </div>
            {data.red_flags.length ? (
              <>
                <h4 className="subheading">Things to watch</h4>
                <ul className="profile-list">
                  {data.red_flags.map((flag) => (
                    <li key={flag}>{flag}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </section>

          {breakdown.length ? (
            <section className="panel">
              <h3>Score breakdown</h3>
              <div className="metric-list">
                {breakdown.map((item) => (
                  <Metric key={item.label} label={item.label} value={formatNumber(item.value)} />
                ))}
              </div>
            </section>
          ) : null}

          {about.length ? (
            <section className="panel">
              <h3>About the role</h3>
              <div className="metric-list">
                {about.map((item) => (
                  <Metric key={item.label} label={item.label} value={capitalise(String(item.value))} />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function capitalise(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

function Metric({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="metric-row">
      <span className="muted">{label}</span>
      <strong>{value ?? "Not available"}</strong>
    </div>
  );
}
