"use client";

import { FormEvent, useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { SkillBadge } from "@/components/SkillBadge";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ScrapeRunStatus, Source, SourceHealthAnalytics, SourceScrapeRunStatus, SourceTestResult } from "@/types/api";

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

const initialJobServeSearch = {
  keywords: "AI",
  location: "London",
  distance: "Within 50 miles",
  select_all_industries: true,
  posted_within: "Within 7 days",
  job_type: "Any",
  remote_only: false,
  max_pages: "3"
};

const quickSearches = ["AI", "AI Engineer", "Data Analyst", "Power BI", "Automation Engineer"];
const distanceOptions = ["Within 1 mile", "Within 5 miles", "Within 10 miles", "Within 15 miles", "Within 25 miles", "Within 50 miles", "Within 75 miles", "Within 100 miles"];
const postedOptions = ["Within 7 days", "Within 6 days", "Within 5 days", "Within 4 days", "Within 3 days", "Within 2 days", "Within 1 day", "Today"];
const jobTypeOptions = ["Any", "Permanent", "Contract", "Contract/Permanent", "Part Time/Temporary/Seasonal"];

export default function SourcesPage() {
  const [health, setHealth] = useState<SourceHealthAnalytics | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [form, setForm] = useState(initialForm);
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<SourceTestResult | null>(null);
  const [scrapeResult, setScrapeResult] = useState<ScrapeRunStatus | null>(null);
  const [jobServeSearch, setJobServeSearch] = useState(initialJobServeSearch);
  const [jobServeResult, setJobServeResult] = useState<SourceScrapeRunStatus | null>(null);
  const [activeScrapeRunId, setActiveScrapeRunId] = useState<number | null>(null);
  const [activeJobServeRunId, setActiveJobServeRunId] = useState<number | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<Source | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceListError, setSourceListError] = useState<string | null>(null);
  const [scrapeRunError, setScrapeRunError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "warning" | "error"; message: string } | null>(null);

  const load = () => {
    setLoading(true);
    Promise.allSettled([api.sourceHealth(), api.sources()])
      .then(([healthResult, sourceResult]) => {
        const errors: string[] = [];
        if (healthResult.status === "fulfilled") {
          setHealth(healthResult.value);
        } else {
          errors.push(`Source health: ${healthResult.reason instanceof Error ? healthResult.reason.message : "request failed"}`);
        }
        if (sourceResult.status === "fulfilled") {
          setSources(sourceResult.value);
          setSelectedSourceId((current) => current ?? sourceResult.value[0]?.id ?? null);
        } else {
          errors.push(`Sources: ${sourceResult.reason instanceof Error ? sourceResult.reason.message : "request failed"}`);
        }
        setSourceListError(errors.length ? errors.join(" | ") : null);
      })
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

  useEffect(() => {
    if (!activeJobServeRunId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.sourceScrapeRun(activeJobServeRunId);
        if (cancelled) {
          return;
        }
        setJobServeResult(result);
        if (result.status === "completed" || result.status === "failed") {
          setActiveJobServeRunId(null);
          setActionLoading(null);
          if (result.status === "failed") {
            setScrapeRunError(result.error ?? "JobServe scrape failed");
          }
          load();
        }
      } catch (err) {
        if (!cancelled) {
          setActiveJobServeRunId(null);
          setActionLoading(null);
          setScrapeRunError(err instanceof Error ? err.message : "Unable to poll JobServe scrape run");
        }
      }
    };
    poll();
    const intervalId = globalThis.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      globalThis.clearInterval(intervalId);
    };
  }, [activeJobServeRunId]);

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

  const searchScrapeJobServe = async (event: FormEvent) => {
    event.preventDefault();
    setActionLoading("jobserve-search");
    setJobServeResult(null);
    setError(null);
    setScrapeRunError(null);
    try {
      const started = await api.searchScrapeJobServe({
        keywords: jobServeSearch.keywords,
        location: jobServeSearch.location || null,
        distance: jobServeSearch.distance,
        select_all_industries: jobServeSearch.select_all_industries,
        posted_within: jobServeSearch.posted_within,
        job_type: jobServeSearch.job_type,
        remote_only: jobServeSearch.remote_only,
        max_pages: Number(jobServeSearch.max_pages || 3)
      });
      setActiveJobServeRunId(started.run_id);
      setJobServeResult({
        run_id: started.run_id,
        status: started.status,
        found: 0,
        created: 0,
        updated: 0,
        skipped: 0,
        error: null,
        search_params: {
          keywords: jobServeSearch.keywords,
          location: jobServeSearch.location,
          distance: jobServeSearch.distance,
          select_all_industries: jobServeSearch.select_all_industries,
          posted_within: jobServeSearch.posted_within,
          job_type: jobServeSearch.job_type,
          remote_only: jobServeSearch.remote_only,
          max_pages: Number(jobServeSearch.max_pages || 3)
        },
        final_search_url: null,
        result_count: 0
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to run JobServe search scrape");
      setActionLoading(null);
    }
  };

  const deleteSource = async (source: Source, deleteJobs: boolean) => {
    setActionLoading(`delete-${source.id}`);
    setError(null);
    setNotice(null);
    try {
      const result = await api.deleteSource(source.id, deleteJobs);
      setDeleteCandidate(null);
      setNotice({
        type: "success",
        message: result.deleted_jobs ? "Source and jobs deleted." : "Source disabled."
      });
      load();
    } catch (err) {
      console.error("Source delete failed", err);
      setError(deleteJobs ? "Unable to delete source and jobs. Please retry." : "Unable to disable source. Please retry.");
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return <LoadingState label="Loading sources" />;
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
        <div className="panel-header">
          <h2>JobServe search scrape</h2>
          <div className="action-row">
            {quickSearches.map((keyword) => (
              <button
                key={keyword}
                type="button"
                className="secondary-button compact-button"
                onClick={() => setJobServeSearch((current) => ({ ...current, keywords: keyword }))}
              >
                {keyword}
              </button>
            ))}
          </div>
        </div>
        <form className="filters-bar" onSubmit={searchScrapeJobServe}>
          <label>
            Keywords
            <input value={jobServeSearch.keywords} onChange={(event) => setJobServeSearch({ ...jobServeSearch, keywords: event.target.value })} required />
          </label>
          <label>
            Location
            <input value={jobServeSearch.location} onChange={(event) => setJobServeSearch({ ...jobServeSearch, location: event.target.value })} />
          </label>
          <label>
            Distance
            <select
              value={jobServeSearch.distance}
              onChange={(event) => setJobServeSearch({ ...jobServeSearch, distance: event.target.value })}
            >
              {distanceOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={jobServeSearch.select_all_industries}
              onChange={(event) => setJobServeSearch({ ...jobServeSearch, select_all_industries: event.target.checked })}
            />
            Select all industries
          </label>
          <label>
            Posted
            <select value={jobServeSearch.posted_within} onChange={(event) => setJobServeSearch({ ...jobServeSearch, posted_within: event.target.value })}>
              {postedOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
          <label>
            Job type
            <select value={jobServeSearch.job_type} onChange={(event) => setJobServeSearch({ ...jobServeSearch, job_type: event.target.value })}>
              {jobTypeOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
          <label>
            Max pages
            <input
              type="number"
              min="1"
              max="10"
              value={jobServeSearch.max_pages}
              onChange={(event) => setJobServeSearch({ ...jobServeSearch, max_pages: event.target.value })}
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={jobServeSearch.remote_only}
              onChange={(event) => setJobServeSearch({ ...jobServeSearch, remote_only: event.target.checked })}
            />
            Remote only
          </label>
          <button type="submit" disabled={actionLoading !== null}>
            Search and scrape
          </button>
        </form>
        {actionLoading === "jobserve-search" ? <LoadingState label="Running JobServe search scrape" /> : null}
        {activeJobServeRunId ? <div className="notice-banner info">Scrape running...</div> : null}
        {scrapeRunError ? <div className="notice-banner error">{scrapeRunError}</div> : null}
        {jobServeResult ? (
          <div className="state-card">
            <h3>JobServe result</h3>
            <div className="metric-list">
              <div className="metric-row">
                <span>Search params</span>
                <strong>{formatSearchParams(jobServeResult.search_params)}</strong>
              </div>
              <div className="metric-row">
                <span>Final URL</span>
                <strong>{jobServeResult.final_search_url ?? "Pending"}</strong>
              </div>
              <div className="metric-row">
                <span>Found</span>
                <strong>{jobServeResult.found}</strong>
              </div>
              <div className="metric-row">
                <span>Result count</span>
                <strong>{jobServeResult.result_count ?? jobServeResult.found}</strong>
              </div>
              <div className="metric-row">
                <span>Created</span>
                <strong>{jobServeResult.created}</strong>
              </div>
              <div className="metric-row">
                <span>Updated</span>
                <strong>{jobServeResult.updated}</strong>
              </div>
              <div className="metric-row">
                <span>Skipped</span>
                <strong>{jobServeResult.skipped}</strong>
              </div>
            </div>
            {jobServeResult.diagnostics ? (
              <div className="debug-panel">
                <h4>JobServe debug</h4>
                <div className="metric-list">
                  <div className="metric-row">
                    <span>Page title</span>
                    <strong>{String(jobServeResult.diagnostics.page_title ?? "Unknown")}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Jobs text</span>
                    <strong>{String(jobServeResult.diagnostics.detected_jobs_for_text ?? jobServeResult.diagnostics.visible_result_count_text ?? "None")}</strong>
                  </div>
                  <div className="metric-row">
                    <span>.jobItem count</span>
                    <strong>{String(jobServeResult.diagnostics.job_item_count ?? jobServeResult.diagnostics.left_list_result_cards_detected ?? 0)}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Candidate cards</span>
                    <strong>{String(jobServeResult.diagnostics.possible_result_list_item_count ?? 0)}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Parser mode</span>
                    <strong>{String(jobServeResult.diagnostics.parser_mode ?? "unknown")}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Zero reason</span>
                    <strong>{String(jobServeResult.diagnostics.zero_result_reason ?? "none")}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Cookie banner</span>
                    <strong>{String(Boolean(jobServeResult.diagnostics.cookie_banner_exists ?? jobServeResult.diagnostics.cookie_or_consent_present))}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Blocked flags</span>
                    <strong>
                      {[
                        jobServeResult.diagnostics.captcha_present ? "captcha" : null,
                        jobServeResult.diagnostics.cloudflare_present ? "cloudflare" : null,
                        jobServeResult.diagnostics.access_denied_present ? "access denied" : null,
                        jobServeResult.diagnostics.no_results_present ? "no results" : null
                      ]
                        .filter(Boolean)
                        .join(", ") || "none"}
                    </strong>
                  </div>
                </div>
                {Array.isArray(jobServeResult.diagnostics.artifact_urls) && jobServeResult.diagnostics.artifact_urls.length ? (
                  <div className="artifact-links">
                    {(jobServeResult.diagnostics.artifact_urls as string[]).map((url) => (
                      <a key={url} className="table-link" href={url} target="_blank" rel="noreferrer">
                        {url.endsWith(".png") ? "Open screenshot" : "Open HTML snapshot"}
                      </a>
                    ))}
                  </div>
                ) : null}
                <details>
                  <summary>Visible text</summary>
                  <pre>{String(jobServeResult.diagnostics.visible_text_first_2000 ?? "")}</pre>
                </details>
                <details>
                  <summary>First result texts</summary>
                  <pre>
                    {JSON.stringify(
                      jobServeResult.diagnostics.first_10_job_item_texts ??
                        jobServeResult.diagnostics.first_10_candidate_job_card_texts ??
                        jobServeResult.diagnostics.first_10_visible_left_list_result_texts ??
                        [],
                      null,
                      2
                    )}
                  </pre>
                </details>
              </div>
            ) : null}
            <a className="table-link" href="/jobs">
              View jobs from this source
            </a>
          </div>
        ) : null}
      </section>

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

      {sourceListError ? <div className="notice-banner warning">{sourceListError}</div> : null}
      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}

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
                <th>Actions</th>
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
                  <td>
                    {source ? (
                      <button type="button" className="danger-button compact-button" onClick={() => setDeleteCandidate(source)}>
                        Delete
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {deleteCandidate ? (
        <div className="modal-backdrop">
          <div className="modal-panel">
            <div className="modal-header">
              <div>
                <h2>Delete source</h2>
                <p className="muted-text">{deleteCandidate.name}</p>
              </div>
              <button type="button" className="secondary-button compact-button" onClick={() => setDeleteCandidate(null)}>
                Close
              </button>
            </div>
            <div className="row-actions">
              <button type="button" className="secondary-button" disabled={actionLoading !== null} onClick={() => void deleteSource(deleteCandidate, false)}>
                Disable source only
              </button>
              <button type="button" className="danger-button" disabled={actionLoading !== null} onClick={() => void deleteSource(deleteCandidate, true)}>
                Delete source and jobs
              </button>
            </div>
          </div>
        </div>
      ) : null}
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

function formatSearchParams(params: Record<string, unknown> | undefined): string {
  if (!params || Object.keys(params).length === 0) {
    return "Pending";
  }
  return [
    params.keywords,
    params.location,
    params.distance,
    params.posted_within,
    params.job_type,
    params.select_all_industries ? "All industries" : "Default industries",
    params.remote_only ? "Remote only" : "All work modes",
    params.max_pages ? `Max ${params.max_pages} pages` : null
  ]
    .filter(Boolean)
    .join(" | ");
}
