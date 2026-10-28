"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScorePill } from "@/components/ScoreBadge";
import { api } from "@/lib/api";
import { SOURCE_LABELS } from "@/lib/applications";
import { formatDate } from "@/lib/format";
import type { Application, ApplicationSource, ApplicationStatus, ApplicationsList } from "@/types/api";

type Tab = "to_apply" | "applied" | "skipped";

const SUBMITTED: ApplicationStatus[] = ["applied", "interviewing", "offer", "rejected"];

const PROGRESS_LABELS: Record<string, string> = {
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Not successful"
};

export default function ApplicationsPage() {
  const [data, setData] = useState<ApplicationsList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "error" | "info"; message: string } | null>(null);
  const [tab, setTab] = useState<Tab>("to_apply");
  const [threshold, setThreshold] = useState(80);
  const [adding, setAdding] = useState(false);
  const [showLogForm, setShowLogForm] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    const result = await api.applications();
    setData(result);
    return result;
  }, []);

  useEffect(() => {
    refresh()
      .then((result) => setThreshold(result.minimum_apply_score))
      .catch((err: Error) => setError(err.message));
  }, [refresh]);

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    return {
      // Best matches first; jobs logged from other sites (no score) go last.
      to_apply: items
        .filter((item) => item.status === "to_apply")
        .sort((a, b) => Number(b.total_score ?? -1) - Number(a.total_score ?? -1)),
      applied: items.filter((item) => SUBMITTED.includes(item.status)),
      skipped: items.filter((item) => item.status === "skipped")
    };
  }, [data]);

  const addTopMatches = async () => {
    setAdding(true);
    setNotice(null);
    try {
      const result = await api.prepareApplications(threshold);
      await refresh();
      setTab("to_apply");
      setNotice({
        type: result.added ? "success" : "info",
        message: result.added
          ? `Added ${result.added} ${result.added === 1 ? "job" : "jobs"} scoring ${result.minimum_apply_score} or more.`
          : `No new jobs scoring ${result.minimum_apply_score} or more. Try a lower score, or find more jobs on the Sources page.`
      });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't add jobs." });
    } finally {
      setAdding(false);
    }
  };

  const update = async (item: Application, status: ApplicationStatus, message: string) => {
    setBusyId(item.id);
    setNotice(null);
    try {
      await api.updateApplication(item.id, { status });
      await refresh();
      setNotice({ type: "success", message });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't update that application." });
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (item: Application) => {
    if (!window.confirm(`Remove "${item.title}" from your list?`)) {
      return;
    }
    setBusyId(item.id);
    try {
      await api.deleteApplication(item.id);
      await refresh();
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't remove that application." });
    } finally {
      setBusyId(null);
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!data) {
    return <LoadingState label="Loading your applications" />;
  }

  const rows = groups[tab];

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Your apply list</h2>
            <p className="muted-text">
              Open a job and choose <strong>Apply with Claude</strong>. Claude fills in the form in your own browser, and you check it and press
              Submit.
            </p>
          </div>
        </div>
        <div className="action-row">
          <label>
            Add my matches scoring
            <select value={threshold} onChange={(event) => setThreshold(Number(event.target.value))}>
              {[60, 70, 80, 85, 90].map((value) => (
                <option key={value} value={value}>
                  {value} or more
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="align-end" disabled={adding} onClick={() => void addTopMatches()}>
            {adding ? <span className="spinner" aria-hidden="true" /> : null}
            {adding ? "Adding..." : "Add top matches"}
          </button>
          <button type="button" className="secondary-button align-end" onClick={() => setShowLogForm((open) => !open)}>
            {showLogForm ? "Close" : "Add a job from LinkedIn, Indeed..."}
          </button>
        </div>
        {showLogForm ? (
          <LogApplicationForm
            onSaved={async (status) => {
              setShowLogForm(false);
              await refresh();
              setTab(status === "applied" ? "applied" : "to_apply");
              setNotice({ type: "success", message: status === "applied" ? "Application logged." : "Added to your apply list." });
            }}
          />
        ) : null}
      </section>

      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}

      <div className="tab-row" role="tablist" aria-label="Application status">
        {(
          [
            ["to_apply", "To apply"],
            ["applied", "Applied"],
            ["skipped", "Skipped"]
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "tab active" : "tab"}
            onClick={() => setTab(key)}
          >
            {label} <span className="tab-count">{groups[key].length}</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={tab === "to_apply" ? "Nothing to apply for yet" : tab === "applied" ? "No applications yet" : "Nothing skipped"}
          message={
            tab === "to_apply"
              ? "Add your top matches above, or pick jobs from the Jobs page."
              : tab === "applied"
                ? "Jobs you apply for, here or on other sites, will show up here."
                : "Jobs you skip are kept here in case you change your mind."
          }
        />
      ) : (
        <section className="panel">
          <div className="table-wrap">
            <table className="data-table stack-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>{tab === "applied" ? "Applied" : "Match"}</th>
                  <th>{tab === "applied" ? "Progress" : "Where"}</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td data-label="Job">
                      <div className="job-cell">
                        {item.job_id ? (
                          <Link href={`/jobs/${item.job_id}`} className="table-link">
                            {item.title}
                          </Link>
                        ) : item.url ? (
                          <a href={item.url} target="_blank" rel="noreferrer" className="table-link">
                            {item.title} ↗
                          </a>
                        ) : (
                          <strong>{item.title}</strong>
                        )}
                        <span className="job-meta">{[item.company, item.location].filter(Boolean).join(" · ")}</span>
                        {item.notes ? <span className="status-note">{item.notes}</span> : null}
                      </div>
                    </td>
                    {tab === "applied" ? (
                      <td data-label="Applied" className="muted-text">
                        {formatDate(item.applied_at)}
                      </td>
                    ) : (
                      <td data-label="Match">
                        {item.total_score !== null ? (
                          <div className="action-row">
                            <ScorePill score={item.total_score} />
                            <RecommendationBadge tier={item.recommendation_tier} />
                          </div>
                        ) : (
                          <span className="muted-text">Not scored</span>
                        )}
                      </td>
                    )}
                    {tab === "applied" ? (
                      <td data-label="Progress">
                        <select
                          aria-label={`Progress for ${item.title}`}
                          value={item.status}
                          disabled={busyId === item.id}
                          onChange={(event) => void update(item, event.target.value as ApplicationStatus, "Progress updated.")}
                        >
                          {Object.entries(PROGRESS_LABELS).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </td>
                    ) : (
                      <td data-label="Where" className="muted-text">
                        {SOURCE_LABELS[item.source] ?? item.source}
                        {item.availability_status && ["expired", "unavailable"].includes(item.availability_status) ? (
                          <span className="status-note">Listing may have closed</span>
                        ) : null}
                      </td>
                    )}
                    <td data-label="Actions">
                      <div className="row-actions">
                        {tab === "to_apply" ? (
                          <Link href={`/apply/${item.id}`} className="button-link compact-button">
                            Apply with Claude
                          </Link>
                        ) : null}
                        <details className="menu">
                          <summary className="icon-button" aria-label={`More actions for ${item.title}`}>
                            ⋯
                          </summary>
                          <div className="menu-panel">
                            {tab === "to_apply" ? (
                              <>
                                <button type="button" onClick={(event) => closeMenuAfter(event, () => void update(item, "applied", "Marked as applied."))}>
                                  Mark as applied
                                </button>
                                <button type="button" onClick={(event) => closeMenuAfter(event, () => void update(item, "skipped", "Skipped."))}>
                                  Skip this job
                                </button>
                              </>
                            ) : (
                              <button
                                type="button"
                                onClick={(event) => closeMenuAfter(event, () => void update(item, "to_apply", "Moved back to your apply list."))}
                              >
                                Move back to apply list
                              </button>
                            )}
                            <hr />
                            <button type="button" className="danger" onClick={(event) => closeMenuAfter(event, () => void remove(item))}>
                              Remove from list
                            </button>
                          </div>
                        </details>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function LogApplicationForm({ onSaved }: { onSaved: (status: ApplicationStatus) => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [url, setUrl] = useState("");
  const [source, setSource] = useState<ApplicationSource>("linkedin");
  const [status, setStatus] = useState<ApplicationStatus>("applied");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.createApplication({ title, company: company || undefined, url: url || undefined, source, status });
      await onSaved(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that application.");
      setSaving(false);
    }
  };

  return (
    <form className="profile-form log-form" onSubmit={submit}>
      <div className="form-grid">
        <label>
          Job title
          <input value={title} onChange={(event) => setTitle(event.target.value)} required />
        </label>
        <label>
          Company
          <input value={company} onChange={(event) => setCompany(event.target.value)} />
        </label>
        <label>
          Link to the job
          <input type="url" placeholder="https://" value={url} onChange={(event) => setUrl(event.target.value)} />
        </label>
        <label>
          Found on
          <select value={source} onChange={(event) => setSource(event.target.value as ApplicationSource)}>
            {Object.entries(SOURCE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => setStatus(event.target.value as ApplicationStatus)}>
            <option value="applied">I&apos;ve applied</option>
            <option value="to_apply">I want to apply</option>
          </select>
        </label>
      </div>
      {error ? <div className="notice-banner error">{error}</div> : null}
      <div className="action-row">
        <button type="submit" disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </form>
  );
}

function closeMenuAfter(event: React.MouseEvent<HTMLButtonElement>, action: () => void) {
  event.currentTarget.closest("details")?.removeAttribute("open");
  action();
}
