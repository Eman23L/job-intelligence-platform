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
  const github = profile?.cv_text?.match(/github\.com\/([A-Za-z0-9-]+)/i)?.[1] ?? null;
  const employer = currentEmployer(profile?.cv_text ?? "");

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Let Claude apply for you every day</h2>
            <p className="muted-text">
              One daily run in your own Chrome: every 2 days Claude first brings your CV up to date, then it applies to the JobServe jobs
              you&apos;ve allowed, finds more on LinkedIn and Indeed, asks you to approve them, applies, and gives you a report. Everything
              it applies for shows up here.
            </p>
          </div>
          <button
            type="button"
            className={groups.queued.length ? undefined : "secondary-button"}
            onClick={() => setShowInstructions((open) => !open)}
          >
            {showInstructions ? "Hide setup" : "Set up the daily run"}
          </button>
        </div>
        {showInstructions ? (
          <>
            <ol className="steps">
              <li>
                In Chrome, sign in to JobServe, LinkedIn and Indeed, and upload your latest CV to each. Claude uses the CV saved on each
                site.
              </li>
              <li>
                Choose which JobServe jobs Claude can apply to without asking on <Link href="/profile#wanted">My CV</Link>. Claude works
                from <Link href="/claude">Claude&apos;s daily page</Link>.
              </li>
              <li>
                Open the Claude in Chrome side panel, paste the instructions below and run them once while you watch. Already set up?
                Replace your saved shortcut with these, which now include the CV refresh.
              </li>
              <li>
                Save them as a shortcut and schedule it every weekday (for example 9am, after the 6:30am search) with the clock icon. It
                runs while Chrome is open and your computer is on.
              </li>
              <li>
                Each run Claude shows you the LinkedIn and Indeed jobs it found. Reply <strong>approve all</strong>, or the numbers you
                want, and it applies and reports back.
              </li>
            </ol>
            <CopyBlock label="Instructions for Claude" text={claudeInstructions(origin, roles, place, github, employer)} rows={14} />
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

      {tab === "applied" ? <WeeklyReport items={groups.applied} /> : null}

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

function WeeklyReport({ items }: { items: Application[] }) {
  const since = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const recent = items.filter((item) => item.applied_at && new Date(item.applied_at).getTime() >= since);
  const bySite = recent.reduce<Record<string, number>>((totals, item) => {
    const site = SOURCE_LABELS[item.source] ?? item.source;
    totals[site] = (totals[site] ?? 0) + 1;
    return totals;
  }, {});
  return (
    <div className="notice-banner info">
      <strong>Last 7 days:</strong> {recent.length} {recent.length === 1 ? "application" : "applications"}
      {recent.length
        ? ` (${Object.entries(bySite)
            .map(([site, count]) => `${site} ${count}`)
            .join(" · ")})`
        : ""}
      .
    </div>
  );
}

// The employer of the job on the CV that runs to the present, e.g. "Software Engineer, Acme, UK (2021 to present)" gives "Acme".
function currentEmployer(cvText: string): string | null {
  for (const raw of cvText.split("\n")) {
    const line = raw.trim();
    if (line.length > 160 || /^[-*•]/.test(line) || !/\b(present|current|now)\b/i.test(line)) {
      continue;
    }
    const head = line
      .replace(/\([^()]*\)\s*$/, "")
      .replace(/\b(19|20)\d{2}\b.*$/, "")
      .replace(/[\s,|:\u2013\u2014-]+$/, "")
      .trim();
    const at = head.match(/\bat\s+(.+)$/i);
    const name = (at ? at[1] : head.split(/\s*[,|]\s*/)[1])?.trim();
    if (name && name.length > 1 && name.length < 60) {
      return name;
    }
  }
  return null;
}

function claudeInstructions(origin: string, roles: string[], place: string, github: string | null, employer: string | null): string {
  const wanted = roles.length ? roles.join(", ") : "the roles on my CV";
  const where = place === "remote" ? "remote" : `near ${place}`;
  const repos = github ? `new or updated repositories on github.com/${github} (read their READMEs)` : "my GitHub, if my CV links to it";
  return [
    "Daily job applications. Use this Chrome window, where I'm signed in to JobServe, LinkedIn and Indeed.",
    "",
    `1. Open ${origin}/claude. It has my details, my CV, the jobs I want and today's jobs from my searches. Use only the details on that page, and don't change anything there except with the buttons these steps mention.`,
    '2. If "Refresh my CV" on that page says it\'s due, update my CV before applying:',
    `   - Look for anything new since it was last checked: the news from me shown there, ${repos}, and anything you remember about my recent work from our chats. Don't use emails or other websites for this.`,
    "   - Add only what you've seen evidence of: new projects, tools I've actually used, courses, certificates, a new role. Keep everything else exactly as it is: my name, contact details, employers, job titles and dates. Never invent numbers, dates, skills or results.",
    '   - Edit "My whole updated CV", write each change on its own line under "What changed", and press "Save updated CV". If there\'s nothing worth adding, press "Nothing new".',
    '3. Apply straight away to every job marked "Apply automatically" under "Today\'s jobs".',
    "4. Then find more:",
    `   - LinkedIn Jobs: search each of these roles ${where}, Easy Apply only, posted in the past 24 hours: ${wanted}.`,
    `   - Indeed: search the same roles ${where}, posted in the last day.`,
    "   Leave out anything that isn't one of those roles, or that's already under \"Applied in the last 7 days\".",
    '5. Show me one numbered list of the LinkedIn and Indeed jobs plus the "Ask first" jobs on my page: title, company, site, and one line on why it fits. Ask me: "Reply \'approve all\', or the numbers to apply for." Wait for my reply.',
    "6. Apply to the ones I approve.",
    "",
    "How to apply to each job:",
    `- Never apply to a job at my current employer (from the job on my CV that runs to the present${employer ? `: ${employer}` : ""}) or any other name it trades under, even if it's marked "Apply automatically". That includes agency jobs that name them or clearly describe them. Skip these and note why.`,
    "- Use the site's own application (JobServe's Apply, LinkedIn Easy Apply, Indeed's Apply) and the CV already saved on that site.",
    "- Fill in every field from my details only. Answer screening questions honestly from my CV, and never invent experience, qualifications or numbers.",
    '- For a cover letter or "why this job" box, write under 150 words, specific to the job, in plain British English with no emojis.',
    "- If it sends you to a company website, needs a new account, shows a CAPTCHA, needs a file the site doesn't already have, or asks something my details don't cover, don't guess: skip it and note why.",
    "- Otherwise check the answers and submit.",
    `- Record it on ${origin}/claude: "Mark as applied" next to a job from my page, or "Log a LinkedIn or Indeed application" for the others. Use "Skip" for a job from my page that you skipped.`,
    "",
    "7. Finish with a short report: what you applied for (title, company, site), what you skipped and why, and anything that needs me. If you updated my CV, say what changed and remind me to upload the new PDF from My CV to JobServe, LinkedIn and Indeed.",
    "",
    "Apply to no more than 10 jobs in one run. Don't message anyone, follow companies, change my profiles or settings, or pay for anything."
  ].join("\n");
}
