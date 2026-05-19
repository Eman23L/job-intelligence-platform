import Link from "next/link";
import type { JobListItem } from "@/types/api";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScoreBadge } from "@/components/ScoreBadge";
import { formatDate, formatSalary, formatSalaryPeriod } from "@/lib/format";

export function JobsTable({ jobs }: { jobs: JobListItem[] }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Company</th>
            <th>Location</th>
            <th>Remote</th>
            <th>Salary</th>
            <th>Posted</th>
            <th>Role family</th>
            <th>Tier</th>
            <th>Score</th>
            <th>Skills</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                <Link href={`/jobs/${job.id}`} className="table-link">
                  {job.title}
                </Link>
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
              <td>{job.role_family ?? "Unanalysed"}</td>
              <td>
                <RecommendationBadge tier={job.recommendation_tier} />
              </td>
              <td>
                <ScoreBadge score={job.total_score} />
              </td>
              <td>
                <span className="compact-counts">
                  {job.matched_skills_count} matched / {job.missing_skills_count} missing
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
