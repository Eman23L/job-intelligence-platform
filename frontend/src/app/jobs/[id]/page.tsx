"use client";

import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScoreBadge } from "@/components/ScoreBadge";
import { SkillBadge } from "@/components/SkillBadge";
import { api } from "@/lib/api";
import { formatDate, formatSalary, formatSalaryPeriod } from "@/lib/format";
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

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>{data.job.title}</h2>
            <p className="muted">
              {data.job.company_name ?? "Unknown company"} / {data.job.location ?? "No location"} /{" "}
              {formatSalary(data.job.normalized_annual_min, data.job.normalized_annual_max, data.job.salary_currency)}
              {" annual"}
              {" / Raw "}
              {formatSalary(data.job.salary_min_raw, data.job.salary_max_raw, data.job.salary_currency)}
              {formatSalaryPeriod(data.job.salary_period) ? ` per ${formatSalaryPeriod(data.job.salary_period)}` : ""}
            </p>
          </div>
          <div className="action-row">
            <button type="button" onClick={() => runAction("save")}>
              Save
            </button>
            <button type="button" className="secondary-button" onClick={() => runAction("reject")}>
              Reject
            </button>
            <button type="button" className="secondary-button" onClick={() => runAction("applied")}>
              Mark applied
            </button>
          </div>
        </div>
        {actionMessage ? <p className="muted">{actionMessage}</p> : null}
        <div className="badge-list">
          <RecommendationBadge tier={data.score?.recommendation_tier} />
          <ScoreBadge score={data.score?.total_score} />
          <SkillBadge label={`Saved status: ${data.saved_status ?? "none"}`} />
          <SkillBadge label={`Posted: ${formatDate(data.job.posted_at)}`} />
        </div>
      </section>

      <div className="detail-grid">
        <section className="panel">
          <h3>Description</h3>
          <p className="description">{data.job.description_text ?? "No description stored."}</p>
        </section>
        <section className="panel">
          <h3>Score</h3>
          <div className="metric-list">
            <Metric label="Role match" value={data.score?.role_match_score} />
            <Metric label="Skill match" value={data.score?.skill_match_score} />
            <Metric label="Experience" value={data.score?.experience_score} />
            <Metric label="Location" value={data.score?.location_score} />
            <Metric label="Salary" value={data.score?.salary_score} />
            <Metric label="Freshness" value={data.score?.freshness_score} />
          </div>
          {data.score?.explanation ? <p className="description">{data.score.explanation}</p> : null}
        </section>
      </div>

      <div className="detail-grid">
        <section className="panel">
          <h3>Analysis</h3>
          <div className="metric-list">
            <Metric label="Role family" value={data.analysis?.role_family} />
            <Metric label="Seniority" value={data.analysis?.seniority_level} />
            <Metric label="Role focus" value={data.analysis?.role_focus} />
          </div>
        </section>
        <section className="panel">
          <h3>Red flags</h3>
          <div className="badge-list">
            {data.red_flags.length ? data.red_flags.map((flag) => <SkillBadge key={flag} label={flag} tone="warn" />) : <span className="muted">None</span>}
          </div>
        </section>
      </div>

      <div className="detail-grid">
        <section className="panel">
          <h3>Matched skills</h3>
          <div className="badge-list">
            {data.matched_skills.map((skill) => (
              <SkillBadge key={skill.id} label={skill.skill_name} tone="good" />
            ))}
          </div>
        </section>
        <section className="panel">
          <h3>Missing skills</h3>
          <div className="badge-list">
            {data.missing_skills.map((skill) => (
              <SkillBadge key={skill.id} label={`${skill.skill_name} / ${skill.learning_priority ?? "medium"}`} tone="warn" />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="metric-row">
      <span className="muted">{label}</span>
      <strong>{value ?? "Not available"}</strong>
    </div>
  );
}
