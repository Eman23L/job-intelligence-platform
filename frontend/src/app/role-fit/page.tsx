"use client";

import { useEffect, useState } from "react";
import { BarList } from "@/components/BarList";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import type { RoleFitAnalytics } from "@/types/api";

export default function RoleFitPage() {
  const [data, setData] = useState<RoleFitAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .roleFit()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <LoadingState label="Loading role fit" />;
  }
  if (data.items.length === 0) {
    return <EmptyState title="No roles analysed yet" message="Once jobs are collected and analysed, you'll see how well you fit each type of role." />;
  }

  const items = [...data.items].sort((a, b) => Number(b.average_score ?? -1) - Number(a.average_score ?? -1));

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Average match score by role</h2>
          <p className="muted-text">Higher scores mean your CV lines up better with what these roles ask for.</p>
        </div>
      </div>
      <BarList
        items={items.map((item) => {
          const score = item.average_score === null ? null : Number(item.average_score);
          return {
            key: item.role_family ?? "unknown",
            label: item.role_family ?? "Unknown",
            detail: `· ${item.count} ${item.count === 1 ? "job" : "jobs"}${tierSummary(item.recommendation_tiers)}`,
            value: score === null ? "Not scored" : Math.round(score),
            fraction: (score ?? 0) / 100,
            tone: score === null ? "neutral" : score >= 85 ? "good" : score >= 70 ? "accent" : score >= 40 ? "warn" : "danger"
          };
        })}
      />
    </section>
  );
}

function tierSummary(tiers: Record<string, number>): string {
  const good = (tiers["Excellent match"] ?? 0) + (tiers["Strong match"] ?? 0);
  return good > 0 ? ` · ${good} good ${good === 1 ? "match" : "matches"}` : "";
}
