"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScorePill } from "@/components/ScoreBadge";
import { useIsOwner } from "@/lib/account-context";
import { api } from "@/lib/api";
import { SOURCE_LABELS } from "@/lib/applications";
import { formatDate } from "@/lib/format";
import type { FindResult, FoundJob } from "@/types/api";

type Notice = { type: "success" | "error" | "info"; message: string; link?: { href: string; label: string } };

export default function FindJobsPage() {
  const [result, setResult] = useState<FindResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const wasSearching = useRef(false);
  const isOwner = useIsOwner();

  const load = useCallback(async () => {
    const next = await api.find();
    setResult(next);
    return next;
  }, []);

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, [load]);

  // While a search runs, check back every 10 seconds and show the results when it finishes.
  const searching = result?.search.searching ?? false;
  useEffect(() => {
    if (!searching) {
      return;
    }
    wasSearching.current = true;
    const timer = window.setInterval(() => {
      load()
        .then((next) => {
          if (!next.search.searching && wasSearching.current) {
            wasSearching.current = false;
            const fresh = next.jobs.filter((job) => job.is_new && !job.status).length;
            setNotice({
              type: "success",
              message: fresh
                ? `Search finished: ${fresh} new ${fresh === 1 ? "job" : "jobs"} for you.`
                : "Search finished. Nothing new this time."
            });
          }
        })
        .catch(() => undefined);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [searching, load]);

  const groups = useMemo(() => {
    const jobs = result?.jobs ?? [];
    return {
      toDecide: jobs.filter((job) => !job.status),
      queued: jobs.filter((job) => job.status === "to_apply"),
      applied: jobs.filter((job) => job.status && job.status !== "to_apply")
    };
  }, [result]);

  const newSearch = async () => {
    setNotice(null);
    try {
      await api.newSearch();
      setNotice({ type: "info", message: "Searching now. This takes a few minutes; you can leave this page and come back." });
      await load();
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't start a search." });
    }
  };

  const apply = async (jobs: FoundJob[]) => {
    setBusy(true);
    setNotice(null);
    try {
      const { changed } = await api.applyToJobs(jobs.map((job) => job.job_id));
      await load();
      window.dispatchEvent(new Event("applications-changed"));
      setNotice({
        type: "success",
        message: `${changed} ${changed === 1 ? "job is" : "jobs are"} queued. Claude fills in each application in your browser, and you can see when it's done.`,
        link: { href: "/applications", label: "Start applying" }
      });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't queue those jobs." });
    } finally {
      setBusy(false);
    }
  };

  const dismiss = async (job: FoundJob) => {
    setBusy(true);
    try {
      await api.dismissJobs([job.job_id]);
      await load();
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't hide that job." });
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!result) {
    return <LoadingState label="Finding your jobs" />;
  }

  const { search } = result;
  const ready = result.has_cv && search.roles.length > 0;

  return (
    <div className="page-stack">
      {!ready ? <GetStarted hasCv={result.has_cv} hasRoles={search.roles.length > 0} /> : null}

      {ready ? (
        <section className="panel search-summary">
          <div>
            <span className="eyebrow">Searching for</span>
            <div className="badge-list">
              {search.roles.map((role) => (
                <span key={role} className="badge strong">
                  {role}
                </span>
              ))}
            </div>
            <p className="muted-text">
              {search.remote_only ? "Remote jobs" : search.location ? `Near ${search.location}` : "Anywhere in the UK"} ·{" "}
              <Link href="/profile#wanted">Change</Link>
            </p>
            <p className="muted-text">
              {search.searching
                ? "Searching now…"
                : search.last_finished_at
                  ? `Last searched ${formatWhen(search.last_finished_at)}.`
                  : "Not searched yet."}{" "}
              {search.next_run_at ? `Searches again automatically ${formatWhen(search.next_run_at)}.` : null}
            </p>
            {isOwner && (!search.feeds.reed || !search.feeds.adzuna) ? (
              <p className="muted-text">Searching JobServe. Add Reed and Adzuna API keys on the server to find more jobs.</p>
            ) : null}
          </div>
          <button type="button" disabled={search.searching} onClick={() => void newSearch()}>
            {search.searching ? <span className="spinner" aria-hidden="true" /> : null}
            {search.searching ? "Searching..." : "New search"}
          </button>
        </section>
      ) : null}

      {notice ? (
        <div className={`notice-banner ${notice.type}`}>
          {notice.message} {notice.link ? <Link href={notice.link.href}>{notice.link.label} →</Link> : null}
        </div>
      ) : null}

      {ready ? (
        result.jobs.length === 0 ? (
          <section className="panel empty-panel">
            <h2>{search.searching ? "Looking for jobs…" : "No jobs found yet"}</h2>
            <p className="muted-text">
              {search.searching
                ? "The first results usually arrive within a few minutes."
                : "Press New search, or add more roles or a wider area on your CV page."}
            </p>
          </section>
        ) : (
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Jobs for you</h2>
                <p className="muted-text">
                  {groups.toDecide.length} to look at · {groups.queued.length} queued for Claude · {groups.applied.length} applied. Best
                  matches to your CV first.
                </p>
              </div>
              {groups.toDecide.length ? (
                <button type="button" disabled={busy} onClick={() => void apply(groups.toDecide)}>
                  Apply to all ({groups.toDecide.length})
                </button>
              ) : null}
            </div>
            <div className="table-wrap">
              <table className="data-table stack-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Match</th>
                    <th aria-label="Status" />
                  </tr>
                </thead>
                <tbody>
                  {result.jobs.map((job) => (
                    <tr key={job.job_id} className={job.status ? "row-done" : undefined}>
                      <td data-label="Job">
                        <div className="job-cell">
                          <span>
                            <Link href={`/jobs/${job.job_id}`} className="table-link">
                              {job.title}
                            </Link>
                            {job.is_new && !job.status ? <span className="badge info inline-badge">New</span> : null}
                          </span>
                          <span className="job-meta">
                            {[job.company, job.location, job.salary, SOURCE_LABELS[job.source] ?? null].filter(Boolean).join(" · ")}
                          </span>
                        </div>
                      </td>
                      <td data-label="Match">
                        {job.total_score !== null ? (
                          <div className="action-row">
                            <ScorePill score={job.total_score} />
                            <RecommendationBadge tier={job.recommendation_tier} />
                          </div>
                        ) : (
                          <span className="muted-text">Scoring…</span>
                        )}
                      </td>
                      <td data-label="Status">
                        <JobAction job={job} busy={busy} onApply={() => void apply([job])} onDismiss={() => void dismiss(job)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )
      ) : null}
    </div>
  );
}

function JobAction({ job, busy, onApply, onDismiss }: { job: FoundJob; busy: boolean; onApply: () => void; onDismiss: () => void }) {
  if (!job.status) {
    return (
      <div className="row-actions">
        <button type="button" className="compact-button" disabled={busy} onClick={onApply}>
          Apply
        </button>
        <button type="button" className="ghost-button compact-button" disabled={busy} onClick={onDismiss}>
          Not for me
        </button>
      </div>
    );
  }
  if (job.status === "to_apply") {
    return (
      <div className="row-actions">
        <span className="badge strong">Queued for Claude</span>
        {job.application_id ? (
          <Link href={`/apply/${job.application_id}`} className="table-link compact-link">
            Apply now
          </Link>
        ) : null}
      </div>
    );
  }
  return <span className="badge good">✓ Applied {job.applied_at ? formatDate(job.applied_at) : ""}</span>;
}

function GetStarted({ hasCv, hasRoles }: { hasCv: boolean; hasRoles: boolean }) {
  const steps = [
    {
      done: hasCv,
      title: "Add your CV",
      text: "Upload it and paste in the text. Every job is matched against it.",
      href: "/profile",
      action: "Add my CV"
    },
    {
      done: hasRoles,
      title: "Choose the jobs you want",
      text: "Type the roles you're after, even ones your CV doesn't show yet, and where you want to work.",
      href: "/profile#wanted",
      action: "Choose jobs"
    },
    { done: false, title: "We find jobs for you", text: "The search runs every weekday morning, or whenever you press New search." },
    {
      done: false,
      title: "Apply",
      text: "Press Apply on a job, or Apply to all. Claude fills in each application and you can see when it's done."
    }
  ];
  const next = steps.findIndex((step) => !step.done);
  return (
    <section className="panel">
      <h2>Get started</h2>
      <ol className="get-started">
        {steps.map((step, index) => (
          <li key={step.title} className={step.done ? "done" : index === next ? "current" : undefined}>
            <span className="step-number">{step.done ? "✓" : index + 1}</span>
            <div>
              <strong>{step.title}</strong>
              <p className="muted-text">{step.text}</p>
              {index === next && step.href ? (
                <Link href={step.href} className="button-link compact-button">
                  {step.action}
                </Link>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(
    date
  );
}
