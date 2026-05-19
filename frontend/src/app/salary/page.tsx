"use client";

import { useEffect, useState } from "react";
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
    return <LoadingState label="Loading salary analytics" />;
  }

  return (
    <div className="page-stack">
      <div className="stats-grid">
        <StatCard label="Average salary min" value={formatSalary(data.average_salary_min, null)} />
        <StatCard label="Average salary max" value={formatSalary(null, data.average_salary_max)} />
        <StatCard label="Missing salary" value={data.missing_salary_count} />
      </div>
      <div className="detail-grid">
        <SalaryPanel title="Salary by role family" rows={data.salary_by_role_family} />
        <SalaryPanel title="Salary by remote type" rows={data.salary_by_remote_type} />
      </div>
    </div>
  );
}

function SalaryPanel({ title, rows }: { title: string; rows: SalaryGroup[] }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      <div className="metric-list">
        {rows.map((row) => (
          <div className="metric-row" key={`${title}-${row.group ?? "unknown"}`}>
            <span>{row.group ?? "Unknown"} / {row.count}</span>
            <strong>{formatSalary(row.average_salary_min, row.average_salary_max)}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}
