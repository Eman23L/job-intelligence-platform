import Link from "next/link";
import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import type { JobListItem } from "@/types/api";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScorePill } from "@/components/ScoreBadge";
import { PendingScoringBadge } from "@/components/PendingScoringBadge";
import { formatDate, formatSalary, formatSalaryPeriod } from "@/lib/format";

export function JobsTable({
  jobs,
  selectedIds,
  onToggle,
  onToggleAll,
  onDelete,
  onExclude,
  onScorecard,
  onCheckAvailability
}: {
  jobs: JobListItem[];
  selectedIds: Set<number>;
  onToggle: (jobId: number, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onDelete: (jobId: number) => void;
  onExclude: (jobId: number) => void;
  onScorecard: (jobId: number) => void;
  onCheckAvailability: (jobId: number) => Promise<void>;
}) {
  const allSelected = jobs.length > 0 && jobs.every((job) => selectedIds.has(job.id));

  return (
    <div className="table-wrap">
      <table className="data-table jobs-table">
        <thead>
          <tr>
            <th className="select-cell">
              <input
                type="checkbox"
                aria-label="Select all jobs on this page"
                checked={allSelected}
                onChange={(event) => onToggleAll(event.target.checked)}
              />
            </th>
            <th>Job</th>
            <th>Match</th>
            <th>Salary</th>
            <th>Posted</th>
            <th>Status</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td className="select-cell">
                <input
                  type="checkbox"
                  aria-label={`Select ${job.title}`}
                  checked={selectedIds.has(job.id)}
                  onChange={(event) => onToggle(job.id, event.target.checked)}
                />
              </td>
              <td className="td-job">
                <div className="job-cell">
                  <Link href={`/jobs/${job.id}`} className="table-link">
                    {job.title}
                  </Link>
                  <span className="job-meta">{jobMeta(job)}</span>
                </div>
              </td>
              <td className="td-match">
                {job.total_score === null ? (
                  <PendingScoringBadge />
                ) : (
                  <div className="action-row">
                    <ScorePill score={job.total_score} />
                    <div className="cell-stack">
                      <RecommendationBadge tier={job.recommendation_tier} />
                      <span className="compact-counts">{skillSummary(job)}</span>
                    </div>
                  </div>
                )}
              </td>
              <td className="td-salary">
                <SalaryCell job={job} />
              </td>
              <td className="td-posted muted-text">{formatDate(job.posted_at)}</td>
              <td className="td-status">
                <StatusCell job={job} />
              </td>
              <td className="td-actions">
                <div className="row-actions">
                  <button type="button" className="secondary-button compact-button" onClick={() => onScorecard(job.id)}>
                    Why this score?
                  </button>
                  <details className="menu">
                    <summary className="icon-button" aria-label={`More actions for ${job.title}`}>
                      ⋯
                    </summary>
                    <div className="menu-panel">
                      <button type="button" onClick={(event) => closeMenuAfter(event, () => void onCheckAvailability(job.id))}>
                        Check it&apos;s still open
                      </button>
                      <button type="button" onClick={(event) => closeMenuAfter(event, () => onExclude(job.id))}>
                        Hide from results
                      </button>
                      <hr />
                      <button type="button" className="danger" onClick={(event) => closeMenuAfter(event, () => onDelete(job.id))}>
                        Delete job
                      </button>
                    </div>
                  </details>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function jobMeta(job: JobListItem): string {
  const remote = job.remote_type ? job.remote_type.charAt(0).toUpperCase() + job.remote_type.slice(1) : null;
  const locationSaysRemote = job.location?.toLowerCase().includes("remote") && job.remote_type === "remote";
  return [job.company_name, job.location, locationSaysRemote ? null : remote].filter(Boolean).join(" · ");
}

function skillSummary(job: JobListItem): string {
  if (job.missing_skills_count === 0) {
    return job.matched_skills_count > 0 ? "All key skills matched" : "";
  }
  return `${job.missing_skills_count} skill${job.missing_skills_count === 1 ? "" : "s"} missing`;
}

function SalaryCell({ job }: { job: JobListItem }) {
  const annual = formatSalary(job.normalized_annual_min, job.normalized_annual_max, job.salary_currency);
  const period = formatSalaryPeriod(job.salary_period);
  // Only show the advertised rate when it differs from the annualised figure (day and hourly rates).
  const showRaw = period && period !== "year" && (job.salary_min_raw || job.salary_max_raw);
  if (annual === "Not listed") {
    return <span className="muted-text">Not listed</span>;
  }
  return (
    <div className="cell-stack">
      <span>{annual}</span>
      {showRaw ? (
        <span className="muted-text">
          {formatSalary(job.salary_min_raw, job.salary_max_raw, job.salary_currency)} / {period}
        </span>
      ) : null}
    </div>
  );
}

function StatusCell({ job }: { job: JobListItem }) {
  if (job.status === "excluded") {
    return <span className="badge excluded">Hidden</span>;
  }
  if (job.application_status && job.application_status !== "not_started") {
    return <span className="badge strong">{formatLabel(job.application_status)}</span>;
  }
  // "Active" and "unknown" are the normal state, so only surface availability when something is wrong.
  if (job.availability_status && !["active", "unknown"].includes(job.availability_status)) {
    return (
      <span title={job.availability_reason ?? undefined}>
        <AvailabilityBadge status={job.availability_status} />
      </span>
    );
  }
  return <span className="muted-text">Open</span>;
}

function formatLabel(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function closeMenuAfter(event: React.MouseEvent<HTMLButtonElement>, action: () => void) {
  event.currentTarget.closest("details")?.removeAttribute("open");
  action();
}
