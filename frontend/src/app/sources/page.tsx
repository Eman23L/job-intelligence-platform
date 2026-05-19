"use client";

import { FormEvent, useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { SkillBadge } from "@/components/SkillBadge";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ScrapeRunStatus, Source, SourceHealthAnalytics, SourceTestResult } from "@/types/api";

const initialForm = {
  name: "",
  base_url: "",
  source_type: "careers",
  permission_notes: "",
  scraping_allowed: false,
  rate_limit_per_minute: "8",
  allowed_path_patterns: "",
  job_link_patterns: "",
  start_url: ""
};

export default function SourcesPage() {
  const [health, setHealth] = useState<SourceHealthAnalytics | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [form, setForm] = useState(initialForm);
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<SourceTestResult | null>(null);
  const [scrapeResult, setScrapeResult] = useState<ScrapeRunStatus | null>(null);
  const [activeScrapeRunId, setActiveScrapeRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    Promise.all([api.sourceHealth(), api.sources()])
      .then(([healthResult, sourceResult]) => {
        setHealth(healthResult);
        setSources(sourceResult);
        setSelectedSourceId((current) => current ?? sourceResult[0]?.id ?? null);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  useEffect(() => {
    if (!activeScrapeRunId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.scrapeRun(activeScrapeRunId);
        if (cancelled) {
          return;
        }
        setScrapeResult({ ...result, scrape_run_id: activeScrapeRunId });
        if (result.status === "completed" || result.status === "failed") {
          setActiveScrapeRunId(null);
          setActionLoading(null);
          load();
        }
      } catch (err) {
        if (!cancelled) {
          setActiveScrapeRunId(null);
          setActionLoading(null);
          setError(err instanceof Error ? err.message : "Unable to poll scrape run");
        }
      }
    };
    poll();
    const intervalId = globalThis.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      globalThis.clearInterval(intervalId);
    };
  }, [activeScrapeRunId]);

  const createSource = async (event: FormEvent) => {
    event.preventDefault();
    setActionLoading("create");
    setError(null);
    try {
      const created = await api.createSourceFromUrl({
        name: form.name,
        base_url: form.base_url,
        source_type: form.source_type,
        permission_notes: form.permission_notes,
        scraping_allowed: form.scraping_allowed,
        rate_limit_per_minute: Number(form.rate_limit_per_minute || 8),
        allowed_path_patterns: splitPatterns(form.allowed_path_patterns),
        job_link_patterns: splitPatterns(form.job_link_patterns)
      });
      setSelectedSourceId(created.id);
      setForm({ ...initialForm, source_type: form.source_type });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create source");
    } finally {
      setActionLoading(null);
    }
  };

  const testSource = async () => {
    if (!selectedSourceId) {
      return;
    }
    setActionLoading("test");
    setTestResult(null);
    setError(null);
    try {
      setTestResult(await api.testSourceUrl(selectedSourceId, form.start_url));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to test source");
    } finally {
      setActionLoading(null);
    }
  };

  const scrapeSource = async (dryRun: boolean) => {
    if (!selectedSourceId) {
      return;
    }
    setActionLoading(dryRun ? "dry-run" : "scrape");
    setScrapeResult(null);
    setError(null);
    try {
      const started = await api.scrapeSourceNow(selectedSourceId, {
        start_url: form.start_url || null,
        max_pages: 10,
        max_jobs: 20,
        delay_seconds: 8,
        dry_run: dryRun
      });
      setActiveScrapeRunId(started.scrape_run_id);
      setScrapeResult({
        scrape_run_id: started.scrape_run_id,
        status: "pending",
        jobs_found: 0,
        jobs_created: 0,
        jobs_updated: 0,
        jobs_skipped: 0,
        parsed_jobs: [],
        errors: []
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to run scrape");
      setActionLoading(null);
    }
  };

  if (loading) {
    return <LoadingState label="Loading sources" />;
  }
  if (error && !sources.length) {
    return <ErrorState message={error} />;
  }

  const sourceById = new Map(sources.map((source) => [source.id, source]));
  const healthBySourceId = new Map((health?.items ?? []).map((item) => [item.source_id, item]));
  const rows = [
    ...sources.map((source) => ({ source, healthItem: healthBySourceId.get(source.id) })),
    ...(health?.items ?? [])
      .filter((item) => !sourceById.has(item.source_id))
      .map((item) => ({ source: undefined, healthItem: item }))
  ];

  return (
    <div className="page-stack">
      <section className="panel">
        <h2>Add permitted source</h2>
        <form className="filters-bar" onSubmit={createSource}>
          <label>
            Name
            <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required />
          </label>
          <label>
            URL
            <input value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required />
          </label>
          <label>
            Type
            <input value={form.source_type} onChange={(event) => setForm({ ...form, source_type: event.target.value })} />
          </label>
          <label>
            Rate/min
            <input
              type="number"
              min="1"
              value={form.rate_limit_per_minute}
              onChange={(event) => setForm({ ...form, rate_limit_per_minute: event.target.value })}
            />
          </label>
          <label>
            Allowed patterns
            <input
              value={form.allowed_path_patterns}
              onChange={(event) => setForm({ ...form, allowed_path_patterns: event.target.value })}
              placeholder="/careers, /jobs"
            />
          </label>
          <label>
            Job link patterns
            <input
              value={form.job_link_patterns}
              onChange={(event) => setForm({ ...form, job_link_patterns: event.target.value })}
              placeholder="/jobs/, /vacancies/"
            />
          </label>
          <label>
            Permission notes
            <input
              value={form.permission_notes}
              onChange={(event) => setForm({ ...form, permission_notes: event.target.value })}
              required
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={form.scraping_allowed}
              onChange={(event) => setForm({ ...form, scraping_allowed: event.target.checked })}
            />
            Scraping allowed
          </label>
          <button type="submit" disabled={actionLoading === "create"}>
            Add source
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Test and scrape</h2>
          <div className="action-row">
            <button type="button" onClick={testSource} disabled={!selectedSourceId || actionLoading !== null}>
              Test
            </button>
            <button type="button" className="secondary-button" onClick={() => scrapeSource(true)} disabled={!selectedSourceId || actionLoading !== null}>
              Dry run
            </button>
            <button type="button" className="secondary-button" onClick={() => scrapeSource(false)} disabled={!selectedSourceId || actionLoading !== null}>
              Scrape now
            </button>
          </div>
        </div>
        <div className="filters-bar">
          <label>
            Source
            <select value={selectedSourceId ?? ""} onChange={(event) => setSelectedSourceId(Number(event.target.value))}>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Optional start URL
            <input value={form.start_url} onChange={(event) => setForm({ ...form, start_url: event.target.value })} />
          </label>
        </div>
        {error ? <ErrorState message={error} /> : null}
        {actionLoading ? <LoadingState label={activeScrapeRunId ? `Scrape ${activeScrapeRunId} ${scrapeResult?.status ?? "pending"}` : `Starting ${actionLoading}`} /> : null}
        {testResult ? <ResultPanel title="Test result" result={testResult} /> : null}
        {scrapeResult ? <ResultPanel title="Scrape result" result={scrapeResult} /> : null}
      </section>

      <section className="panel">
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Enabled</th>
                <th>Permission</th>
                <th>Jobs</th>
                <th>Last scrape</th>
                <th>Status</th>
                <th>Created / updated</th>
                <th>Errors</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ source, healthItem }) => (
                <tr key={source?.id ?? healthItem?.source_id}>
                  <td>{source?.name ?? healthItem?.source_name ?? "Unknown source"}</td>
                  <td>
                    <SkillBadge label={source?.enabled ? "enabled" : "disabled"} tone={source?.enabled ? "good" : "neutral"} />
                  </td>
                  <td>
                    <SkillBadge label={source?.scraping_allowed ? "allowed" : "not allowed"} tone={source?.scraping_allowed ? "good" : "warn"} />
                  </td>
                  <td>{healthItem?.jobs_count ?? 0}</td>
                  <td>{formatDate(healthItem?.last_scrape_started_at)}</td>
                  <td>{healthItem?.scrape_status ?? "No runs"}</td>
                  <td>
                    {healthItem?.jobs_created ?? 0} / {healthItem?.jobs_updated ?? 0}
                  </td>
                  <td>{healthItem?.error_message ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ResultPanel({ title, result }: { title: string; result: SourceTestResult | ScrapeRunStatus }) {
  return (
    <div className="state-card">
      <h3>{title}</h3>
      <pre className="result-json">{JSON.stringify(result, null, 2)}</pre>
    </div>
  );
}

function splitPatterns(value: string) {
  const items = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : null;
}
