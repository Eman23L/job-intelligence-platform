"use client";

import { useEffect, useState } from "react";
import { BarList } from "@/components/BarList";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { StatCard } from "@/components/StatCard";
import { api } from "@/lib/api";
import { formatSalary } from "@/lib/format";
import type { SalaryAnalytics, SalaryGroup } from "@/types/api";

export default function SalaryPage() {
  const [data, setData] = useState<SalaryAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .salary()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <LoadingState label="Loading salary insights" />;
  }

  const groups = [...data.salary_by_role_family, ...data.salary_by_remote_type];
  if (groups.length === 0) {
    return <EmptyState title="No salary data yet" message="Salaries will appear here once jobs with advertised pay have been collected." />;
  }
  // One shared scale so bars are comparable across both panels.
  const scaleMax = Math.max(...groups.map((row) => Number(row.average_salary_max ?? row.average_salary_min ?? 0)), 1);

  return (
    <div className="page-stack">
      <div className="stats-grid">
        <StatCard label="Typical range" value={formatSalary(data.average_salary_min, data.average_salary_max)} hint="Average advertised min – max" />
        <StatCard label="Jobs without a salary" value={data.missing_salary_count} />
      </div>
      <div className="two-column">
        <SalaryPanel title="By role" rows={data.salary_by_role_family} scaleMax={scaleMax} />
        <SalaryPanel title="By work style" rows={data.salary_by_remote_type} scaleMax={scaleMax} />
      </div>
    </div>
  );
}

function SalaryPanel({ title, rows, scaleMax }: { title: string; rows: SalaryGroup[]; scaleMax: number }) {
  const sorted = [...rows].sort((a, b) => Number(b.average_salary_max ?? 0) - Number(a.average_salary_max ?? 0));
  return (
    <section className="panel">
      <h2>{title}</h2>
      <BarList
        items={sorted.map((row) => {
          const min = Number(row.average_salary_min ?? row.average_salary_max ?? 0);
          const max = Number(row.average_salary_max ?? row.average_salary_min ?? 0);
          return {
            key: row.group ?? "unknown",
            label: capitalise(row.group ?? "Unknown"),
            detail: `· ${row.count} ${row.count === 1 ? "job" : "jobs"}`,
            value: formatSalary(row.average_salary_min, row.average_salary_max),
            start: min / scaleMax,
            fraction: max / scaleMax
          };
        })}
      />
    </section>
  );
}

function capitalise(value: string): string {
  const text = value === "onsite" ? "on-site" : value;
  return text.charAt(0).toUpperCase() + text.slice(1);
}
