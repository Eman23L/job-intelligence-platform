"use client";

import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
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

  return (
    <section className="panel">
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Role family</th>
              <th>Jobs</th>
              <th>Average score</th>
              <th>Recommendation tiers</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.role_family ?? "unknown"}>
                <td>{item.role_family ?? "Unknown"}</td>
                <td>{item.count}</td>
                <td>{formatNumber(item.average_score)}</td>
                <td>
                  <div className="badge-list">
                    {Object.entries(item.recommendation_tiers).map(([tier, count]) => (
                      <span key={tier}>
                        <RecommendationBadge tier={tier} /> <span className="muted">{count}</span>
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
