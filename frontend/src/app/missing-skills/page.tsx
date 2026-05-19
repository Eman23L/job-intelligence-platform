"use client";

import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { SkillBadge } from "@/components/SkillBadge";
import { api } from "@/lib/api";
import type { SkillGapAnalytics, SkillGapItem } from "@/types/api";

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
    return <LoadingState label="Loading missing skills" />;
  }

  return (
    <div className="detail-grid">
      <SkillPanel title="Missing skill frequency" items={data.missing_skill_frequency} />
      <SkillPanel title="High priority missing skills" items={data.high_priority_missing_skills} tone="warn" />
      <SkillPanel title="Top 10 learning priorities" items={data.top_10_learning_priorities} tone="warn" />
    </div>
  );
}

function SkillPanel({ title, items, tone = "neutral" }: { title: string; items: SkillGapItem[]; tone?: "neutral" | "warn" }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      <div className="metric-list">
        {items.map((item) => (
          <div className="metric-row" key={`${title}-${item.skill_name}`}>
            <SkillBadge label={`${item.skill_name} / ${item.highest_priority ?? "n/a"}`} tone={tone} />
            <strong>{item.count}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}
