"use client";

import { useEffect, useState } from "react";
import { BarList } from "@/components/BarList";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import type { SkillGapAnalytics, SkillGapItem } from "@/types/api";

const PRIORITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

export default function MissingSkillsPage() {
  const [data, setData] = useState<SkillGapAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .skillGaps()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <LoadingState label="Loading skill gaps" />;
  }

  // The API returns overlapping views of the same list; show it once, most important first.
  const gaps = [...data.missing_skill_frequency].sort(
    (a, b) => rank(b) - rank(a) || b.count - a.count || a.skill_name.localeCompare(b.skill_name)
  );
  const requested = data.skills_linked_to_most_jobs.slice(0, 10);

  if (gaps.length === 0 && requested.length === 0) {
    return <EmptyState title="No skill data yet" message="Add your CV and score some jobs to see which skills you're missing." />;
  }

  const gapMax = Math.max(...gaps.map((item) => item.count), 1);
  const requestedMax = Math.max(...requested.map((item) => item.count), 1);

  return (
    <div className="two-column">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Skills to learn</h2>
            <p className="muted-text">Missing from your CV, ordered by how important they are to the jobs asking for them.</p>
          </div>
        </div>
        {gaps.length === 0 ? (
          <p className="muted">No gaps found. Your CV covers the skills your matched jobs ask for.</p>
        ) : (
          <BarList
            items={gaps.map((item) => ({
              key: item.skill_name,
              label: item.skill_name,
              detail: <PriorityTag priority={item.highest_priority} />,
              value: `${item.count} ${item.count === 1 ? "job" : "jobs"}`,
              fraction: item.count / gapMax,
              tone: item.highest_priority === "high" ? "danger" : item.highest_priority === "medium" ? "warn" : "neutral"
            }))}
          />
        )}
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Most requested skills</h2>
            <p className="muted-text">Across every job collected, whether or not you have them.</p>
          </div>
        </div>
        <BarList
          items={requested.map((item) => ({
            key: item.skill_name,
            label: item.skill_name,
            value: `${item.count} ${item.count === 1 ? "job" : "jobs"}`,
            fraction: item.count / requestedMax
          }))}
        />
      </section>
    </div>
  );
}

function rank(item: SkillGapItem): number {
  return PRIORITY_RANK[item.highest_priority ?? ""] ?? 0;
}

function PriorityTag({ priority }: { priority: string | null }) {
  if (!priority) {
    return null;
  }
  const tone = priority === "high" ? "poor" : priority === "medium" ? "stretch" : "neutral";
  return <span className={`badge ${tone}`}>{priority} priority</span>;
}
