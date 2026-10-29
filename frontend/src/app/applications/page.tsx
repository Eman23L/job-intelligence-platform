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
import type { Application, ApplicationSource, ApplicationStatus, ApplicationsList, UserProfile } from "@/types/api";

type Tab = "queued" | "applied" | "not_for_me";

const SUBMITTED: ApplicationStatus[] = ["applied", "interviewing", "offer", "rejected"];

const PROGRESS_LABELS: Record<string, string> = {
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Not successful"
};

export default function ApplicationsPage() {
  const [data, setData] = useState<ApplicationsList | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);
  const [tab, setTab] = useState<Tab>("queued");
  const [checking, setChecking] = useState(false);
  const [showLogForm, setShowLogForm] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [origin, setOrigin] = useState("");
  const [showInstructions, setShowInstructions] = useState(false);

  const refresh = useCallback(async () => {
    const result = await api.applications();
    setData(result);
    return result;
  }, []);

  useEffect(() => {
    setOrigin(window.location.origin);
    refresh()
      .then((result) => {
        if (!result.items.some((item) => item.status === "to_apply") && result.items.some((item) => SUBMITTED.includes(item.status))) {
          setTab("applied");
        }
      })
      .catch((err: Error) => setError(err.message));
    api
      .profile()
      .then(setProfile)
      .catch(() => undefined);
  }, [refresh]);

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    const byScore = (a: Application, b: Application) => Number(b.total_score ?? -1) - Number(a.total_score ?? -1);
    return {
      queued: items.filter((item) => item.status === "to_apply").sort(byScore),
      applied: items.filter((item) => SUBMITTED.includes(item.status)),
      not_for_me: items.filter((item) => item.status === "skipped")
    };
  }, [data]);

  const checkStillOpen = async () => {
    setChecking(true);
    setNotice({
      type: "info",
      message: "Checking each listing. This can take a minute or two."
    });
    try {
      const started = await api.checkListingsOpen();
      let run = await api.availabilityRun(started.run_id);
      // Poll for up to 10 minutes; each listing is fetched from the job site in the background.
      for (let attempt = 0; ["running", "queued"].includes(run.status) && attempt < 200; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        run = await api.availabilityRun(started.run_id);
        setNotice({
          type: "info",
          message: `Checking listings: ${run.processed} of ${run.total} done.`
        });
      }
      if (run.status !== "completed") {
        throw new Error(run.error ?? "The check didn't finish. Try again in a few minutes.");
      }
      const { skipped } = await api.skipClosedListings();
      const latest = await refresh();
      window.dispatchEvent(new Event("applications-changed"));
      const left = latest.items.filter((item) => item.status === "to_apply" && item.job_id).length;
      setNotice({
        type: "success",
        message: !skipped
          ? "Every queued listing is still open."
          : left
            ? `Removed ${skipped} closed ${skipped === 1 ? "listing" : "listings"}. The ${left} left ${left === 1 ? "is" : "are"} still open.`
            : `All ${skipped} ${skipped === 1 ? "listing has" : "listings have"} closed. Find fresh ones on the Find jobs page.`
      });
    } catch (err) {
      setNotice({
        type: "error",
        message: err instanceof Error ? err.message : "Couldn't check the listings."
      });
    } finally {
      setChecking(false);
    }
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
      setNotice({
        type: "error",
        message: err instanceof Error ? err.message : "Couldn't update that application."
      });
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
      window.dispatchEvent(new Event("applications-changed"));
    } catch (err) {
      setNotice({
        type: "error",
        message: err instanceof Error ? err.message : "Couldn't remove that application."
      });
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
  const roles = profile?.search_roles?.length ? profile.search_roles : (profile?.preferred_roles ?? []);
  const place = profile?.search_remote_only
    ? "remote"
    : profile?.search_location || profile?.location_preference?.split("/")[0]?.trim() || "the UK";

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Let Claude apply for you</h2>
            <p className="muted-text">
              Claude works in your own Chrome: it applies to your queued jobs, then finds more on LinkedIn and Indeed. It fills in each
              form, you press Submit, and each one shows up as Applied here.
            </p>
          </div>
          <button
            type="button"
            className={groups.queued.length ? undefined : "secondary-button"}
            onClick={() => setShowInstructions((open) => !open)}
          >
            {showInstructions ? "Hide instructions" : "Show instructions for Claude"}
          </button>
        </div>
        {showInstructions ? (
          <>
            <ol className="steps">
              <li>In Chrome, sign in to LinkedIn and Indeed, then open the Claude in Chrome side panel.</li>
              <li>Copy the instructions below and paste them into Claude.</li>
              <li>
                To have it run every morning, save the instructions as a shortcut in Claude in Chrome and schedule it daily with the clock
                icon. It runs while Chrome is open and your computer is on.
              </li>
            </ol>
            <CopyBlock label="Instructions for Claude" text={claudeInstructions(origin, roles, place)} rows={12} />
          </>
        ) : null}
      </section>

      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}

      <div className="tab-row" role="tablist" aria-label="Application status">
        {(
          [
            ["queued", "Queued for Claude"],
            ["applied", "Applied"],
            ["not_for_me", "Not for me"]
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

      {tab === "queued" && groups.queued.some((item) => item.job_id) ? (
        <div className="action-row">
          <button type="button" className="secondary-button" disabled={checking} onClick={() => void checkStillOpen()}>
            {checking ? <span className="spinner" aria-hidden="true" /> : null}
            {checking ? "Checking..." : "Check they're still open"}
          </button>
          <span className="muted-text">Jobs whose listing has closed are removed.</span>
        </div>
      ) : null}

      {tab === "applied" ? (
        <div className="action-row">
          <button type="button" className="secondary-button" onClick={() => setShowLogForm((open) => !open)}>
            {showLogForm ? "Close" : "Add a job you applied for"}
          </button>
          <span className="muted-text">For jobs you applied for on LinkedIn, Indeed or anywhere else.</span>
        </div>
      ) : null}
      {tab === "applied" && showLogForm ? (
        <section className="panel">
          <LogApplicationForm
            onSaved={async (status) => {
              setShowLogForm(false);
              await refresh();
              window.dispatchEvent(new Event("applications-changed"));
              setTab(status === "applied" ? "applied" : "queued");
              setNotice({
                type: "success",
                message: status === "applied" ? "Application added." : "Added to your queue."
              });
            }}
          />
        </section>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState
          title={tab === "queued" ? "Nothing queued" : tab === "applied" ? "No applications yet" : "Nothing here"}
          message={
            tab === "queued"
              ? "Press Apply on the Find jobs page to queue jobs for Claude."
              : tab === "applied"
                ? "Jobs Claude applies for, and ones you add yourself, show up here."
                : "Jobs you mark as not for you are kept here in case you change your mind."
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
                        ✓ {formatDate(item.applied_at)}
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
                        {item.availability_status && CLOSED_AVAILABILITY.includes(item.availability_status) ? (
                          <span className="status-note">Listing may have closed</span>
                        ) : null}
                      </td>
                    )}
                    <td data-label="Actions">
                      <div className="row-actions">
                        {tab === "queued" ? (
                          <Link href={`/apply/${item.id}`} className="button-link compact-button">
                            Apply now
                          </Link>
                        ) : null}
                        <details className="menu">
                          <summary className="icon-button" aria-label={`More actions for ${item.title}`}>
                            ⋯
                          </summary>
                          <div className="menu-panel">
                            {tab === "queued" ? (
                              <>
                                <button
                                  type="button"
                                  onClick={(event) => closeMenuAfter(event, () => void update(item, "applied", "Marked as applied."))}
                                >
                                  Mark as applied
                                </button>
                                <button
                                  type="button"
                                  onClick={(event) => closeMenuAfter(event, () => void update(item, "skipped", "Moved to Not for me."))}
                                >
                                  Not for me
                                </button>
                              </>
                            ) : (
                              <button
                                type="button"
                                onClick={(event) => closeMenuAfter(event, () => void update(item, "to_apply", "Queued for Claude again."))}
                              >
                                Queue for Claude again
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
      await api.createApplication({
        title,
        company: company || undefined,
        url: url || undefined,
        source,
        status
      });
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

function claudeInstructions(origin: string, roles: string[], place: string): string {
  const wanted = roles.length ? roles.join(", ") : "the roles on my CV";
  return [
    "Please help me apply for jobs, one at a time.",
    "",
    "Part 1: the jobs I've queued",
    `1. Open ${origin}/applications. The "Queued for Claude" tab lists the jobs I've chosen.`,
    "2. For each job, from the top:",
    '   - Click "Apply now". That page has my details, my CV and the job description. Read it, but don\'t change anything on it.',
    '   - Open the job listing from that page. If it has closed, go back, click "Skip" and move on.',
    "   - Fill in the application using only my details. Answer screening questions honestly from my CV. If a question isn't covered, ask me.",
    '   - For a cover letter or "why this job" box, write a short, specific answer (under 150 words) in plain British English with no emojis.',
    "   - If it needs my CV file, tell me and I'll attach it. If it asks me to create an account or shows a CAPTCHA, stop and ask me.",
    "   - Don't press the final Submit or Apply button. Tell me it's ready and wait for me to submit it.",
    "   - After I've submitted, click \"I've applied\" on the job's page, then start the next one.",
    "",
    "Part 2: LinkedIn and Indeed",
    `3. Search LinkedIn Jobs (Easy Apply, past week) and Indeed (last 7 days) for: ${wanted}, ${place === "remote" ? "remote" : `near ${place}`}.`,
    "4. Apply to the ones that are one of those roles, the same way as Part 1. Skip any that clearly aren't, and tell me in one line why.",
    `5. After I've submitted each one, open ${origin}/applications, go to the Applied tab, click "Add a job you applied for", and save the job title, company and link, with Found on set to LinkedIn or Indeed.`,
    "",
    "Stop after 10 applications in total, or sooner if I say so. Don't message anyone, follow companies, or change any of my profiles or settings."
  ].join("\n");
}
