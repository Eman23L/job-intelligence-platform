"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { CopyBlock } from "@/components/CandidateDetails";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScorePill } from "@/components/ScoreBadge";
import { api } from "@/lib/api";
import { CLOSED_AVAILABILITY, SOURCE_LABELS } from "@/lib/applications";
import { formatDate } from "@/lib/format";
import type { Application, ApplicationSource, ApplicationStatus, ApplicationsList } from "@/types/api";

type Tab = "suggested" | "to_apply" | "applied" | "skipped";

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
  const [checking, setChecking] = useState(false);
  const [showLogForm, setShowLogForm] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [deciding, setDeciding] = useState(false);
  const [showBatch, setShowBatch] = useState(false);
  const [origin, setOrigin] = useState("");

  const refresh = useCallback(async () => {
    const result = await api.applications();
    setData(result);
    return result;
  }, []);

  useEffect(() => {
    setOrigin(window.location.origin);
    refresh()
      .then((result) => {
        setThreshold(result.minimum_apply_score);
        // Open on New matches when the daily search has found something.
        if (result.items.some((item) => item.status === "suggested")) {
          setTab("suggested");
        }
      })
      .catch((err: Error) => setError(err.message));
  }, [refresh]);

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    const byScore = (a: Application, b: Application) => Number(b.total_score ?? -1) - Number(a.total_score ?? -1);
    return {
      suggested: items.filter((item) => item.status === "suggested").sort(byScore),
      // Best matches first; jobs logged from other sites (no score) go last.
      to_apply: items
        .filter((item) => item.status === "to_apply")
        .sort(byScore),
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
          : !data?.has_cv
            ? "Add your CV on the Profile page first, so jobs can be matched to you."
            : `No new jobs scoring ${result.minimum_apply_score} or more. Try a lower score, or find more jobs on the Sources page.`
      });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't add jobs." });
    } finally {
      setAdding(false);
    }
  };

  const checkStillOpen = async () => {
    setChecking(true);
    setNotice({ type: "info", message: "Checking each listing. This can take a minute or two." });
    try {
      const started = await api.checkListingsOpen();
      let run = await api.availabilityRun(started.run_id);
      // Poll for up to 10 minutes; each listing is fetched from the job site in the background.
      for (let attempt = 0; ["running", "queued"].includes(run.status) && attempt < 200; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        run = await api.availabilityRun(started.run_id);
        setNotice({ type: "info", message: `Checking listings: ${run.processed} of ${run.total} done.` });
      }
      if (run.status !== "completed") {
        throw new Error(run.error ?? "The check didn't finish. Try again in a few minutes.");
      }
      const { skipped } = await api.skipClosedListings();
      const latest = await refresh();
      const left = latest.items.filter((item) => item.status === "to_apply" && item.job_id).length;
      setNotice({
        type: "success",
        message: !skipped
          ? "Every listing on your apply list is still open."
          : left
            ? `Moved ${skipped} closed ${skipped === 1 ? "listing" : "listings"} to Skipped. The ${left} left ${left === 1 ? "is" : "are"} still open.`
            : `All ${skipped} ${skipped === 1 ? "listing has" : "listings have"} closed, so they're in Skipped. Run a new search on the Sources page to find fresh jobs.`
      });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't check the listings." });
    } finally {
      setChecking(false);
    }
  };

  const decide = async (approve: boolean, ids?: number[]) => {
    setDeciding(true);
    setNotice(null);
    try {
      const { changed } = await api.decideNewMatches(approve, ids);
      setSelected(new Set());
      const latest = await refresh();
      window.dispatchEvent(new Event("applications-changed"));
      const jobs = `${changed} ${changed === 1 ? "job" : "jobs"}`;
      setNotice({
        type: "success",
        message: approve ? `Approved ${jobs}. They're on your apply list, ready for Claude.` : `Dismissed ${jobs}. They're in Skipped.`
      });
      if (!latest.items.some((item) => item.status === "suggested")) {
        setTab("to_apply");
      }
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't update your new matches." });
    } finally {
      setDeciding(false);
    }
  };

  const toggle = (id: number, checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  const update = async (item: Application, status: ApplicationStatus, message: string) => {
    setBusyId(item.id);
    setNotice(null);
    try {
      await api.updateApplication(item.id, { status });
      await refresh();
      window.dispatchEvent(new Event("applications-changed"));
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
              Your <Link href="/daily-search">daily search</Link> puts new jobs in <strong>New matches</strong> each weekday morning. Approve
              the ones you want, then let Claude fill in each application in your own browser while you check it and press Submit.
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
            ["suggested", "New matches"],
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

      {tab === "suggested" && rows.length > 0 ? (
        <div className="action-row">
          <button type="button" disabled={deciding} onClick={() => void decide(true)}>
            {deciding ? <span className="spinner" aria-hidden="true" /> : null}
            Approve all ({rows.length})
          </button>
          <button type="button" className="secondary-button" disabled={deciding || selected.size === 0} onClick={() => void decide(true, [...selected])}>
            Approve selected{selected.size ? ` (${selected.size})` : ""}
          </button>
          <button type="button" className="ghost-button" disabled={deciding || selected.size === 0} onClick={() => void decide(false, [...selected])}>
            Dismiss selected
          </button>
        </div>
      ) : null}

      {tab === "to_apply" && rows.length > 0 ? (
        <div className="action-row">
          <button type="button" onClick={() => setShowBatch((open) => !open)}>
            {showBatch ? "Hide instructions" : `Apply to all ${rows.length} with Claude`}
          </button>
          {groups.to_apply.some((item) => item.job_id) ? (
            <button type="button" className="secondary-button" disabled={checking} onClick={() => void checkStillOpen()}>
              {checking ? <span className="spinner" aria-hidden="true" /> : null}
              {checking ? "Checking..." : "Check they're still open"}
            </button>
          ) : null}
        </div>
      ) : null}

      {tab === "to_apply" && showBatch && rows.length > 0 ? (
        <section className="panel">
          <h2>Apply to your whole list with Claude</h2>
          <ol className="steps">
            <li>Open Claude in Chrome on this page and paste these instructions.</li>
            <li>Claude works down your list, filling in each application from your details and stopping before Submit.</li>
            <li>Check each one and press Submit. Claude marks it as applied and moves on to the next.</li>
          </ol>
          <CopyBlock label="Instructions for Claude" text={batchInstructions(origin, Math.min(rows.length, 10))} rows={14} />
        </section>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState
          title={
            tab === "suggested"
              ? "No new matches right now"
              : tab === "to_apply"
                ? "Nothing to apply for yet"
                : tab === "applied"
                  ? "No applications yet"
                  : "Nothing skipped"
          }
          message={
            tab === "suggested"
              ? "Your daily search adds new jobs here each weekday morning. You can also run it now from the Daily search page."
              : tab === "to_apply"
              ? "Approve some new matches, add your top matches above, or pick jobs from the Jobs page."
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
                  {tab === "suggested" ? (
                    <th className="select-cell">
                      <input
                        type="checkbox"
                        aria-label="Select all new matches"
                        checked={rows.every((item) => selected.has(item.id))}
                        onChange={(event) => setSelected(event.target.checked ? new Set(rows.map((item) => item.id)) : new Set())}
                      />
                    </th>
                  ) : null}
                  <th>Job</th>
                  <th>{tab === "applied" ? "Applied" : "Match"}</th>
                  <th>{tab === "applied" ? "Progress" : "Where"}</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    {tab === "suggested" ? (
                      <td className="select-cell">
                        <input
                          type="checkbox"
                          aria-label={`Select ${item.title}`}
                          checked={selected.has(item.id)}
                          onChange={(event) => toggle(item.id, event.target.checked)}
                        />
                      </td>
                    ) : null}
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
                          <span className="muted-text">{item.source === "linkedin" || item.source === "indeed" ? "Picked by Claude" : "Not scored"}</span>
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
                        {item.availability_status && CLOSED_AVAILABILITY.includes(item.availability_status) ? (
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
                        {tab === "suggested" ? (
                          <button type="button" className="compact-button" disabled={deciding} onClick={() => void decide(true, [item.id])}>
                            Approve
                          </button>
                        ) : null}
                        <details className="menu">
                          <summary className="icon-button" aria-label={`More actions for ${item.title}`}>
                            ⋯
                          </summary>
                          <div className="menu-panel">
                            {tab === "suggested" ? (
                              <button type="button" onClick={(event) => closeMenuAfter(event, () => void decide(false, [item.id]))}>
                                Dismiss
                              </button>
                            ) : tab === "to_apply" ? (
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

function batchInstructions(origin: string, limit: number): string {
  return [
    "Please help me apply for the jobs on my apply list, one at a time, while I watch.",
    "",
    `1. Open my apply list: ${origin}/applications (the "To apply" tab).`,
    "2. For each job, starting from the top:",
    "   - Click \"Apply with Claude\". That page has my details, my CV and the job description. Read it, but don't change anything on it.",
    "   - Open the job listing from that page. If the listing has closed, go back, click \"Skip\" and move on to the next job.",
    "   - Fill in the application using only my details. Answer screening questions honestly from my CV. If a question isn't covered, ask me.",
    "   - For a cover letter or \"why this job\" box, write a short, specific answer (under 150 words) in plain British English with no emojis.",
    "   - If it needs my CV file, tell me and I'll attach it. If it asks me to create an account or shows a CAPTCHA, stop and ask me.",
    "   - Don't press the final Submit or Apply button. Tell me it's ready, and wait for me to check and submit it.",
    "   - After I've submitted, go back to the job's Apply with Claude page, click \"I've applied\", then start the next job.",
    `3. Stop after ${limit} applications, or sooner if I say so.`,
    "",
    "Don't message anyone, follow companies, or change any of my profiles or settings."
  ].join("\n");
}
