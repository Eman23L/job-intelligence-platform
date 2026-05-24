import Link from "next/link";
import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import type { JobListItem } from "@/types/api";
import { RecommendationActionBadge } from "@/components/RecommendationActionBadge";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScoreBadge } from "@/components/ScoreBadge";
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
      <table className="data-table">
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
            <th>Title</th>
            <th>Company</th>
            <th>Location</th>
            <th>Remote</th>
            <th>Salary</th>
            <th>Posted</th>
            <th>Availability</th>
            <th>Role family</th>
            <th>Tier</th>
            <th>Recommendation</th>
            <th>Score</th>
            <th>Skills</th>
            <th>Actions</th>
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
              <td>
                <Link href={`/jobs/${job.id}`} className="table-link">
                  {job.title}
                </Link>
                {job.status === "excluded" ? <span className="status-note">Excluded</span> : null}
              </td>
              <td>{job.company_name ?? "Unknown"}</td>
              <td>{job.location ?? "Not listed"}</td>
              <td>{job.remote_type ?? "Not listed"}</td>
              <td>
                <div>{formatSalary(job.normalized_annual_min, job.normalized_annual_max, job.salary_currency)}</div>
                <span className="muted-text">
                  Raw: {formatSalary(job.salary_min_raw, job.salary_max_raw, job.salary_currency)}
                  {formatSalaryPeriod(job.salary_period) ? ` / ${formatSalaryPeriod(job.salary_period)}` : ""}
                </span>
              </td>
              <td>{formatDate(job.posted_at)}</td>
              <td>
                <AvailabilityBadge status={job.availability_status} />
                <div className="muted-text">{job.last_checked_at ? `Checked ${formatDate(job.last_checked_at)}` : "Not checked"}</div>
                {job.availability_reason ? <div className="muted-text">{job.availability_reason}</div> : null}
              </td>
              <td>{job.role_family ?? "Unanalysed"}</td>
              <td>
                <RecommendationBadge tier={job.recommendation_tier} />
              </td>
              <td>
                <RecommendationActionBadge recommendation={job.recommendation} />
              </td>
              <td>
                <ScoreBadge score={job.total_score} />
              </td>
              <td>
                <span className="compact-counts">
                  {job.matched_skills_count} matched / {job.missing_skills_count} missing
                </span>
              </td>
              <td>
                <div className="row-actions">
                  <button type="button" className="secondary-button compact-button" onClick={() => onScorecard(job.id)}>
                    Scorecard
                  </button>
                  <button type="button" className="secondary-button compact-button" onClick={() => void onCheckAvailability(job.id)}>
                    Check availability
                  </button>
                  <button type="button" className="secondary-button compact-button" onClick={() => onExclude(job.id)}>
                    Exclude
                  </button>
                  <button type="button" className="danger-button compact-button" onClick={() => onDelete(job.id)}>
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
