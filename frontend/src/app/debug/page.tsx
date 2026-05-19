"use client";

import { apiConfig } from "@/lib/api";

export default function DebugPage() {
  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <h2>Runtime debug</h2>
        </div>
        <div className="metric-list">
          <div className="metric-row">
            <span>API base URL</span>
            <strong>{apiConfig.baseUrl || "not configured"}</strong>
          </div>
          <div className="metric-row">
            <span>NEXT_PUBLIC_API_BASE_URL configured</span>
            <strong>{apiConfig.envVarConfigured ? "yes" : "no"}</strong>
          </div>
          <div className="metric-row">
            <span>Fallback used</span>
            <strong>{apiConfig.fallbackUsed ? "yes" : "no"}</strong>
          </div>
          <div className="metric-row">
            <span>Node environment</span>
            <strong>{apiConfig.nodeEnv}</strong>
          </div>
        </div>
      </section>
    </div>
  );
}
